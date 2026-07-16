"""
Annisaa Markaz - Davomat Bot
=============================
Ustozlar Telegram orqali davomat belgilaydi, natija Notionga yoziladi.

Muallif: Claude (Sirojiddin uchun)
"""

import os
import logging
from datetime import datetime, timedelta, date

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ─────────────────────────────────────────────
#  SOZLAMALAR (Railway Variables dan olinadi)
# ─────────────────────────────────────────────

BOT_TOKEN = os.environ["BOT_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]

# Notion database ID lari
DB_USTOZLAR = "11b88936578b468d991915cd5b527120"
DB_GURUHLAR = "10f5fce8a0b1451384a3d67c7bb99b9d"
DB_TOLOVLAR = "64408559326f421e830d066a24024233"
DB_TOLIBALAR = "4cf46df646394da3bfd2d7147ffde767"
DB_DAVOMAT = "39ddd6064e4380afb4cddde1fab7947b"

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# Data source ID lari (yangi Notion API uchun) - avtomatik topiladi
DATA_SOURCE_CACHE = {}

# Hafta kunlari: Notion nomi -> Python weekday raqami
HAFTA_KUNLARI = {
    "Dushanba": 0,
    "Seshanba": 1,
    "Chorshanba": 2,
    "Payshanba": 3,
    "Juma": 4,
    "Shanba": 5,
    "Yakshanba": 6,
}

OYLAR = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  NOTION YORDAMCHI FUNKSIYALAR
# ─────────────────────────────────────────────

async def notion_query(database_id: str, filter_obj=None):
    """Notion bazasidan yozuvlarni oladi (barcha sahifalarni)."""
    results = []
    payload = {"page_size": 100}
    if filter_obj:
        payload["filter"] = filter_obj

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r = await client.post(
                f"{NOTION_API}/databases/{database_id}/query",
                headers=NOTION_HEADERS,
                json=payload,
            )
            if r.status_code == 404:
                raise RuntimeError(
                    f"Baza topilmadi (404).\n"
                    f"ID: {database_id}\n\n"
                    f"Sabab: integration bu bazaga ulanmagan yoki ID noto'g'ri.\n"
                    f"/tekshir buyrug'ini yuboring."
                )
            r.raise_for_status()
            data = r.json()
            results.extend(data["results"])
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data["next_cursor"]
    return results


async def notion_search_databases():
    """Integration ko'ra oladigan barcha bazalarni topadi (diagnostika uchun)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{NOTION_API}/search",
            headers=NOTION_HEADERS,
            json={
                "filter": {"property": "object", "value": "database"},
                "page_size": 100,
            },
        )
        r.raise_for_status()
        return r.json()["results"]


async def notion_get_page(page_id: str):
    """Bitta Notion sahifasini oladi."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{NOTION_API}/pages/{page_id}", headers=NOTION_HEADERS
        )
        r.raise_for_status()
        return r.json()


async def notion_create_page(database_id: str, properties: dict):
    """Notion bazasiga yangi yozuv qo'shadi."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{NOTION_API}/pages",
            headers=NOTION_HEADERS,
            json={
                "parent": {"database_id": database_id},
                "properties": properties,
            },
        )
        r.raise_for_status()
        return r.json()


def title_matn(page, prop_name):
    """Title ustunidan matn oladi."""
    try:
        arr = page["properties"][prop_name]["title"]
        return arr[0]["plain_text"] if arr else "(nomsiz)"
    except (KeyError, IndexError):
        return "(nomsiz)"


# ─────────────────────────────────────────────
#  MA'LUMOT OLISH
# ─────────────────────────────────────────────

async def ustozni_top(telegram_id: int):
    """Telegram ID orqali ustozni topadi."""
    natija = await notion_query(
        DB_USTOZLAR,
        {"property": "Telegram ID", "number": {"equals": telegram_id}},
    )
    return natija[0] if natija else None


async def ustoz_guruhlari(ustoz_page):
    """Ustozning guruhlarini oladi (yopilganlarni tashlab)."""
    guruh_refs = ustoz_page["properties"].get("Guruhlar", {}).get("relation", [])
    guruhlar = []
    for ref in guruh_refs:
        g = await notion_get_page(ref["id"])

        nom = title_matn(g, "Guruh nomi")

        # 1-filtr: Status
        status = g["properties"].get("Status", {}).get("status")
        status_nomi = status["name"] if status else ""
        if status_nomi == "Guruh yopilgan":
            continue

        # 2-filtr: nomida "yopilgan" so'zi bo'lsa
        if "yopilgan" in nom.lower():
            continue

        guruhlar.append(g)
    return guruhlar


async def guruh_talabalari(guruh_page, debug=None):
    """
    Guruhdagi faol talabalarni oladi.
    Yo'l: Guruh -> To'lovlar -> Toliba ismi -> Talaba
    Faqat Faoliyat = "O'qiyabdi" bo'lganlar.
    """
    props = guruh_page["properties"]

    # To'lovlar relation ustunini topamiz (nomi turlicha bo'lishi mumkin)
    tolov_refs = []
    topilgan_ustun = None
    for nom, qiymat in props.items():
        if qiymat.get("type") == "relation" and "lov" in nom.lower():
            tolov_refs = qiymat["relation"]
            topilgan_ustun = nom
            break

    if debug is not None:
        debug["tolov_ustuni"] = topilgan_ustun
        debug["tolov_soni"] = len(tolov_refs)
        debug["relation_ustunlar"] = [
            n for n, v in props.items() if v.get("type") == "relation"
        ]
        debug["faoliyatlar"] = []

    talabalar = {}

    for ref in tolov_refs:
        tolov = await notion_get_page(ref["id"])

        faoliyat = tolov["properties"].get("Faoliyat", {}).get("status")
        faoliyat_nomi = faoliyat["name"] if faoliyat else "(bo'sh)"

        if debug is not None:
            debug["faoliyatlar"].append(faoliyat_nomi)

        if faoliyat_nomi != "O'qiyabdi":
            continue

        toliba_rel = tolov["properties"].get("Toliba ismi", {}).get("relation", [])
        if not toliba_rel:
            continue

        talaba_id = toliba_rel[0]["id"]
        if talaba_id in talabalar:
            continue

        talaba = await notion_get_page(talaba_id)
        talabalar[talaba_id] = {
            "id": talaba_id,
            "ism": title_matn(talaba, "Name"),
            "tg_id": talaba["properties"].get("Telegram ID", {}).get("number"),
        }

    return sorted(talabalar.values(), key=lambda x: x["ism"])


def dars_kunlari_sanalar(guruh_page, nechta=4):
    """
    Guruhning dars kunlaridan oxirgi sanalarni hisoblaydi.
    Bugundan orqaga qarab yaqin dars kunlarini qaytaradi.
    """
    tanlangan = guruh_page["properties"].get("Dars kunlar", {}).get("multi_select", [])
    kun_nomlari = [k["name"] for k in tanlangan if k["name"] in HAFTA_KUNLARI]

    bugun = date.today()

    if not kun_nomlari:
        # Aniq kun belgilanmagan -> oxirgi 4 kunni beramiz
        return [bugun - timedelta(days=i) for i in range(nechta)]

    weekdaylar = {HAFTA_KUNLARI[k] for k in kun_nomlari}
    sanalar = []
    for i in range(21):  # 3 hafta orqaga qaraymiz
        kun = bugun - timedelta(days=i)
        if kun.weekday() in weekdaylar:
            sanalar.append(kun)
        if len(sanalar) >= nechta:
            break
    return sanalar


def sana_matni(d: date):
    """Sanani chiroyli ko'rsatadi: '15-iyul (Chor)'"""
    qisqa = ["Dush", "Sesh", "Chor", "Pay", "Jum", "Shan", "Yak"]
    return f"{d.day}-{OYLAR[d.month - 1]} ({qisqa[d.weekday()]})"


# ─────────────────────────────────────────────
#  BOT BUYRUQLARI
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botni ishga tushirish + Telegram ID ni ko'rsatish."""
    user = update.effective_user
    ustoz = await ustozni_top(user.id)

    if ustoz:
        ism = title_matn(ustoz, "Ustoz ismi")
        await update.message.reply_text(
            f"Assalomu alaykum, {ism}!\n\n"
            f"Davomat belgilash uchun /davomat buyrug'ini yuboring."
        )
    else:
        await update.message.reply_text(
            f"Assalomu alaykum, {user.first_name}!\n\n"
            f"Sizning Telegram ID raqamingiz:\n"
            f"`{user.id}`\n\n"
            f"Bu raqamni markaz ma'muriga yuboring.",
            parse_mode="Markdown",
        )


async def tekshir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnostika: bot qaysi bazalarni ko'ryapti?"""
    kutish = await update.message.reply_text("⏳ Tekshirilmoqda...")

    kerakli = {
        DB_USTOZLAR.replace("-", ""): "🙂 Ustozlar",
        DB_GURUHLAR.replace("-", ""): "🚪 Guruhlar",
        DB_TOLOVLAR.replace("-", ""): "💎 To'lovlar",
        DB_TOLIBALAR.replace("-", ""): "🎓 Tolibalar",
        DB_DAVOMAT.replace("-", ""): "📋 Davomat",
    }

    try:
        bazalar = await notion_search_databases()
    except Exception as e:
        await kutish.edit_text(f"⚠️ Notion API xatosi:\n{e}")
        return

    if not bazalar:
        await kutish.edit_text(
            "❌ Bot birorta ham bazani ko'rmayapti.\n\n"
            "Sabab: integration hech qaysi bazaga ulanmagan.\n"
            "Notionda: baza → ••• → Connections → Davomat bot"
        )
        return

    korinadigan = {}
    for b in bazalar:
        bid = b["id"].replace("-", "")
        nom = "(nomsiz)"
        try:
            t = b.get("title", [])
            if t:
                nom = t[0]["plain_text"]
        except (KeyError, IndexError):
            pass
        korinadigan[bid] = nom

    satrlar = [f"👁 Bot {len(korinadigan)} ta bazani ko'ryapti:\n"]

    for bid, nom in korinadigan.items():
        belgi = "✅" if bid in kerakli else "▪️"
        satrlar.append(f"{belgi} *{nom}*")
        satrlar.append(f"`{bid}`")

    satrlar.append("\n🔍 Kerakli bazalar:\n")
    for bid, nom in kerakli.items():
        holat = "✅ topildi" if bid in korinadigan else "❌ TOPILMADI"
        satrlar.append(f"{holat} — {nom}")

    matn = "\n".join(satrlar)
    if len(matn) > 4000:
        matn = matn[:4000] + "\n..."

    await kutish.edit_text(matn, parse_mode="Markdown")


async def davomat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Davomat jarayonini boshlaydi - guruhlarni ko'rsatadi."""
    user = update.effective_user
    kutish = await update.message.reply_text("⏳ Guruhlaringiz yuklanmoqda...")

    try:
        ustoz = await ustozni_top(user.id)
        if not ustoz:
            await kutish.edit_text(
                f"❌ Siz ustozlar ro'yxatida topilmadingiz.\n\n"
                f"Telegram ID: `{user.id}`\n"
                f"Bu raqamni ma'muriyatga yuboring.",
                parse_mode="Markdown",
            )
            return

        guruhlar = await ustoz_guruhlari(ustoz)
        if not guruhlar:
            await kutish.edit_text("❌ Sizga biriktirilgan faol guruh topilmadi.")
            return

        # Guruhlarni xotirada saqlaymiz
        context.user_data["guruhlar"] = {g["id"]: g for g in guruhlar}

        tugmalar = [
            [InlineKeyboardButton(
                title_matn(g, "Guruh nomi"),
                callback_data=f"g:{g['id'][:8]}"
            )]
            for g in guruhlar
        ]

        await kutish.edit_text(
            "📚 Qaysi guruh?",
            reply_markup=InlineKeyboardMarkup(tugmalar),
        )

    except Exception as e:
        log.exception("davomat xatosi")
        await kutish.edit_text(f"⚠️ Xatolik yuz berdi:\n{e}")


async def guruh_tanlandi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruh tanlandi -> sanalarni ko'rsatadi."""
    q = update.callback_query
    await q.answer()

    qisqa_id = q.data.split(":")[1]
    guruhlar = context.user_data.get("guruhlar", {})
    guruh = next((g for gid, g in guruhlar.items() if gid.startswith(qisqa_id)), None)

    if not guruh:
        await q.edit_message_text("⚠️ Sessiya eskirdi. /davomat ni qayta yuboring.")
        return

    context.user_data["guruh"] = guruh

    sanalar = dars_kunlari_sanalar(guruh)
    context.user_data["sanalar"] = {d.isoformat(): d for d in sanalar}

    tugmalar = [
        [InlineKeyboardButton(f"📅 {sana_matni(d)}", callback_data=f"s:{d.isoformat()}")]
        for d in sanalar
    ]
    tugmalar.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="orqaga")])

    await q.edit_message_text(
        f"📚 *{title_matn(guruh, 'Guruh nomi')}*\n\nQaysi dars kuni?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(tugmalar),
    )


async def sana_tanlandi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sana tanlandi -> talabalar ro'yxatini chiqaradi."""
    q = update.callback_query
    await q.answer()

    sana_str = q.data.split(":", 1)[1]
    context.user_data["sana"] = sana_str

    guruh = context.user_data.get("guruh")
    if not guruh:
        await q.edit_message_text("⚠️ Sessiya eskirdi. /davomat ni qayta yuboring.")
        return

    await q.edit_message_text("⏳ Talabalar yuklanmoqda...")

    try:
        debug = {}
        talabalar = await guruh_talabalari(guruh, debug=debug)
        if not talabalar:
            from collections import Counter
            hisob = Counter(debug.get("faoliyatlar", []))
            faoliyat_matn = "\n".join(
                f"   • {k}: {v} ta" for k, v in hisob.items()
            ) or "   (birorta ham to'lov yo'q)"

            await q.edit_message_text(
                f"❌ Faol talaba topilmadi.\n\n"
                f"🔍 *Tashxis:*\n"
                f"To'lovlar ustuni: `{debug.get('tolov_ustuni')}`\n"
                f"Bog'langan to'lovlar: {debug.get('tolov_soni', 0)} ta\n\n"
                f"Faoliyat holatlari:\n{faoliyat_matn}\n\n"
                f"Guruhdagi relation ustunlar:\n"
                f"`{debug.get('relation_ustunlar')}`\n\n"
                f"_Bot faqat «O'qiyabdi» bo'lganlarni oladi._",
                parse_mode="Markdown",
            )
            return

        # Boshlanishida hammasi "Keldi"
        context.user_data["holatlar"] = {t["id"]: "Keldi" for t in talabalar}
        context.user_data["talabalar"] = talabalar

        await royxatni_chiz(q, context)

    except Exception as e:
        log.exception("sana_tanlandi xatosi")
        await q.edit_message_text(f"⚠️ Xatolik:\n{e}")


BELGILAR = {"Keldi": "✅", "Kelmadi": "❌", "Tatilda": "🌙"}
KEYINGI = {"Keldi": "Kelmadi", "Kelmadi": "Tatilda", "Tatilda": "Keldi"}


async def royxatni_chiz(q, context):
    """Talabalar ro'yxatini tugmalar bilan chizadi."""
    talabalar = context.user_data["talabalar"]
    holatlar = context.user_data["holatlar"]
    guruh = context.user_data["guruh"]
    sana = date.fromisoformat(context.user_data["sana"])

    tugmalar = []
    for i, t in enumerate(talabalar):
        holat = holatlar[t["id"]]
        belgi = BELGILAR[holat]
        tugmalar.append([
            InlineKeyboardButton(f"{belgi} {t['ism']}", callback_data=f"t:{i}")
        ])

    tugmalar.append([InlineKeyboardButton("💾 SAQLASH", callback_data="saqla")])
    tugmalar.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="bekor")])

    keldi = sum(1 for h in holatlar.values() if h == "Keldi")
    kelmadi = sum(1 for h in holatlar.values() if h == "Kelmadi")
    tatil = sum(1 for h in holatlar.values() if h == "Tatilda")

    matn = (
        f"📚 *{title_matn(guruh, 'Guruh nomi')}*\n"
        f"📅 {sana_matni(sana)}\n\n"
        f"Talaba ismini bosib holatini o'zgartiring:\n"
        f"✅ Keldi → ❌ Kelmadi → 🌙 Ta'tilda\n\n"
        f"✅ {keldi}  ❌ {kelmadi}  🌙 {tatil}"
    )

    await q.edit_message_text(
        matn,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(tugmalar),
    )


async def talaba_bosildi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Talaba tugmasi bosildi -> holatni almashtiradi."""
    q = update.callback_query

    i = int(q.data.split(":")[1])
    talabalar = context.user_data.get("talabalar")
    if not talabalar:
        await q.answer("Sessiya eskirdi", show_alert=True)
        return

    t = talabalar[i]
    hozirgi = context.user_data["holatlar"][t["id"]]
    yangi = KEYINGI[hozirgi]
    context.user_data["holatlar"][t["id"]] = yangi

    await q.answer(f"{t['ism']}: {BELGILAR[yangi]} {yangi}")
    await royxatni_chiz(q, context)


async def saqla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Davomatni Notionga yozadi."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("⏳ Notionga saqlanmoqda...")

    try:
        talabalar = context.user_data["talabalar"]
        holatlar = context.user_data["holatlar"]
        guruh = context.user_data["guruh"]
        sana = context.user_data["sana"]
        ustoz = await ustozni_top(q.from_user.id)

        yozildi = 0
        for t in talabalar:
            holat = holatlar[t["id"]]
            props = {
                "Name": {
                    "title": [{"text": {"content": f"{t['ism']} — {sana_matni(date.fromisoformat(sana))}"}}]
                },
                "🎓 Tolibalar": {"relation": [{"id": t["id"]}]},
                "Date": {"date": {"start": sana}},
                "Holat": {"status": {"name": holat}},
                "🚪 Guruhlar": {"relation": [{"id": guruh["id"]}]},
            }
            if ustoz:
                props["🙂 Ustozlar"] = {"relation": [{"id": ustoz["id"]}]}

            await notion_create_page(DB_DAVOMAT, props)
            yozildi += 1

        keldi = sum(1 for h in holatlar.values() if h == "Keldi")
        kelmadi = sum(1 for h in holatlar.values() if h == "Kelmadi")
        tatil = sum(1 for h in holatlar.values() if h == "Tatilda")

        await q.edit_message_text(
            f"✅ *Saqlandi!*\n\n"
            f"📚 {title_matn(guruh, 'Guruh nomi')}\n"
            f"📅 {sana_matni(date.fromisoformat(sana))}\n\n"
            f"✅ Keldi: {keldi}\n"
            f"❌ Kelmadi: {kelmadi}\n"
            f"🌙 Ta'tilda: {tatil}\n\n"
            f"Jami {yozildi} ta yozuv Notionga yozildi.",
            parse_mode="Markdown",
        )
        context.user_data.clear()

    except Exception as e:
        log.exception("saqlash xatosi")
        await q.edit_message_text(f"⚠️ Saqlashda xatolik:\n{e}")


async def bekor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.edit_message_text("❌ Bekor qilindi.\n\nQaytadan: /davomat")


async def orqaga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    guruhlar = context.user_data.get("guruhlar", {})
    tugmalar = [
        [InlineKeyboardButton(
            title_matn(g, "Guruh nomi"), callback_data=f"g:{gid[:8]}"
        )]
        for gid, g in guruhlar.items()
    ]
    await q.edit_message_text(
        "📚 Qaysi guruh?", reply_markup=InlineKeyboardMarkup(tugmalar)
    )


# ─────────────────────────────────────────────
#  ISHGA TUSHIRISH
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("davomat", davomat))
    app.add_handler(CommandHandler("tekshir", tekshir))
    app.add_handler(CallbackQueryHandler(guruh_tanlandi, pattern="^g:"))
    app.add_handler(CallbackQueryHandler(sana_tanlandi, pattern="^s:"))
    app.add_handler(CallbackQueryHandler(talaba_bosildi, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(saqla, pattern="^saqla$"))
    app.add_handler(CallbackQueryHandler(bekor, pattern="^bekor$"))
    app.add_handler(CallbackQueryHandler(orqaga, pattern="^orqaga$"))

    log.info("Bot ishga tushdi ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
