"""
Annisaa Markaz - Davomat Bot
=============================
Ustozlar Telegram orqali davomat belgilaydi, natija Notionga yoziladi.

Muallif: Claude (Sirojiddin uchun)
"""

import os
import logging
from datetime import datetime, timedelta, date, time as dtime
from zoneinfo import ZoneInfo

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
#  SOZLAMALAR (Railway Variables dan olinadi)
# ─────────────────────────────────────────────

BOT_TOKEN = os.environ["BOT_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# Notion database ID lari
DB_USTOZLAR = "11b88936578b468d991915cd5b527120"
DB_GURUHLAR = "10f5fce8a0b1451384a3d67c7bb99b9d"
DB_TOLOVLAR = "64408559326f421e830d066a24024233"
DB_TOLIBALAR = "4cf46df646394da3bfd2d7147ffde767"
DB_DAVOMAT = "39ddd6064e4380afb4cddde1fab7947b"
DB_GRAFIK = "39fdd6064e438063b5d0e50c9326dcb8"

# Toshkent vaqti
TZ = ZoneInfo("Asia/Tashkent")

def bugun():
    """Toshkent vaqti bo'yicha bugungi sana."""
    return datetime.now(TZ).date()

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


async def notion_update_page(page_id: str, properties: dict):
    """Notion sahifasini yangilaydi."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"properties": properties},
        )
        r.raise_for_status()
        return r.json()


async def notion_archive_page(page_id: str):
    """Notion sahifasini arxivlaydi (trash'ga yuboradi, 30 kun tiklash mumkin)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"archived": True},
        )
        r.raise_for_status()
        return r.json()


def md_himoya(matn: str) -> str:
    """Markdown maxsus belgilarini himoyalaydi (ism va matnlar uchun)."""
    if not matn:
        return matn
    for belgi in ["\\", "*", "_", "`", "[", "]"]:
        matn = matn.replace(belgi, "\\" + belgi)
    return matn


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
    """
    Ustozning guruhlarini oladi (yopilganlarni tashlab).
    Guruhlar bazasidan to'g'ridan-to'g'ri filtr bilan - relation limitisiz.
    """
    ustoz_id = ustoz_page["id"]

    guruhlar_hammasi = await notion_query(
        DB_GURUHLAR,
        {"property": "Ustozalar", "relation": {"contains": ustoz_id}},
    )

    guruhlar = []
    for g in guruhlar_hammasi:
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

    return sorted(guruhlar, key=lambda g: title_matn(g, "Guruh nomi"))


async def guruh_talabalari(guruh_page, debug=None):
    """
    Guruhdagi talabalarni oladi:
      - Faoliyat = "O'qiyabdi"     -> oddiy (Keldi)
      - Faoliyat = "Ta'til berildi" -> ta'tilda (Tatilda)
    """
    guruh_id = guruh_page["id"]

    tolovlar = await notion_query(
        DB_TOLOVLAR,
        {
            "and": [
                {"property": "Guruhlar", "relation": {"contains": guruh_id}},
                {
                    "or": [
                        {"property": "Faoliyat", "status": {"equals": "O'qiyabdi"}},
                        {"property": "Faoliyat", "status": {"equals": "Ta'til berildi"}},
                    ]
                },
            ]
        },
    )

    if debug is not None:
        debug["tolov_soni"] = len(tolovlar)
        hammasi = await notion_query(
            DB_TOLOVLAR,
            {"property": "Guruhlar", "relation": {"contains": guruh_id}},
        )
        debug["jami_tolov"] = len(hammasi)
        debug["faoliyatlar"] = []
        for t in hammasi:
            f = t["properties"].get("Faoliyat", {}).get("status")
            debug["faoliyatlar"].append(f["name"] if f else "(bo'sh)")

    talabalar = {}
    for tolov in tolovlar:
        toliba_rel = tolov["properties"].get("Toliba ismi", {}).get("relation", [])
        if not toliba_rel:
            continue

        talaba_id = toliba_rel[0]["id"]
        faoliyat = tolov["properties"].get("Faoliyat", {}).get("status")
        faoliyat_nomi = faoliyat["name"] if faoliyat else ""
        tatilda = faoliyat_nomi == "Ta'til berildi"

        # Agar allaqachon bor bo'lsa: "O'qiyabdi" ustunroq
        if talaba_id in talabalar:
            if not tatilda:
                talabalar[talaba_id]["tatilda"] = False
            continue

        talaba = await notion_get_page(talaba_id)

        talabalar[talaba_id] = {
            "id": talaba_id,
            "ism": title_matn(talaba, "Name"),
            "tg_id": talaba["properties"].get("Telegram ID", {}).get("number"),
            "tatilda": tatilda,
            "tolov_yaratilgan": tolov.get("created_time"),
        }

    # Avval o'qiyotganlar (ism bo'yicha), keyin ta'tildagilar
    natija = sorted(
        talabalar.values(),
        key=lambda x: (x["tatilda"], x["ism"]),
    )
    return natija


def dars_kunlari_sanalar(guruh_page, nechta=4):
    """
    Guruhning dars kunlaridan oxirgi sanalarni hisoblaydi.
    Bugundan orqaga qarab yaqin dars kunlarini qaytaradi.
    """
    tanlangan = guruh_page["properties"].get("Dars kunlar", {}).get("multi_select", [])
    kun_nomlari = [k["name"] for k in tanlangan if k["name"] in HAFTA_KUNLARI]

    bugun_ = bugun()

    if not kun_nomlari:
        # Aniq kun belgilanmagan -> oxirgi 4 kunni beramiz
        return [bugun_ - timedelta(days=i) for i in range(nechta)]

    weekdaylar = {HAFTA_KUNLARI[k] for k in kun_nomlari}
    sanalar = []
    for i in range(21):  # 3 hafta orqaga qaraymiz
        kun = bugun_ - timedelta(days=i)
        if kun.weekday() in weekdaylar:
            sanalar.append(kun)
        if len(sanalar) >= nechta:
            break
    return sanalar


def sana_matni(d: date):
    """Sanani chiroyli ko'rsatadi: '15-iyul (Chor)'"""
    qisqa = ["Dush", "Sesh", "Chor", "Pay", "Jum", "Shan", "Yak"]
    return f"{d.day}-{OYLAR[d.month - 1]} ({qisqa[d.weekday()]})"


def guruh_vaqti(guruh_page):
    """Guruhning dars vaqtini oladi."""
    v = guruh_page["properties"].get("Dars vaqti", {})
    if v.get("type") == "select" and v.get("select"):
        return v["select"]["name"]
    if v.get("type") == "rich_text" and v.get("rich_text"):
        return v["rich_text"][0]["plain_text"]
    return ""


def bugun_darsmi(guruh_page, kun: date):
    """Shu kuni guruhda dars bormi?"""
    tanlangan = guruh_page["properties"].get("Dars kunlar", {}).get("multi_select", [])
    nomlar = [k["name"] for k in tanlangan]

    if "Har kuni" in nomlar:
        return True

    for n in nomlar:
        if n in HAFTA_KUNLARI and HAFTA_KUNLARI[n] == kun.weekday():
            return True
    return False


# ─────────────────────────────────────────────
#  DARSLAR GRAFIGI
# ─────────────────────────────────────────────

async def grafik_topish(guruh_id: str, sana: str):
    """Shu guruh+sana uchun grafik yozuvini topadi."""
    natija = await notion_query(
        DB_GRAFIK,
        {
            "and": [
                {"property": "🚪 Guruhlar", "relation": {"contains": guruh_id}},
                {"property": "Sana", "date": {"equals": sana}},
            ]
        },
    )
    return natija[0] if natija else None


async def grafik_yozish(guruh_page, ustoz_page, sana: str, holat: str,
                        sabab=None, izoh=None):
    """
    Grafik yozuvini yaratadi yoki yangilaydi.
    holat: "Belgilanmagan" | "Dars boldi" | "Dars qoldirildi"
    """
    guruh_id = guruh_page["id"]
    nom = title_matn(guruh_page, "Guruh nomi")
    d = date.fromisoformat(sana)

    props = {
        "Name": {"title": [{"text": {"content": f"{nom} — {sana_matni(d)}"}}]},
        "🚪 Guruhlar": {"relation": [{"id": guruh_id}]},
        "Sana": {"date": {"start": sana}},
        "Holat": {"status": {"name": holat}},
        "Vaqti": {"rich_text": [{"text": {"content": guruh_vaqti(guruh_page)}}]},
    }
    if ustoz_page:
        props["🙂 Ustozlar"] = {"relation": [{"id": ustoz_page["id"]}]}
    if sabab:
        props["Sabab"] = {"select": {"name": sabab}}
    if izoh:
        props["Izoh"] = {"rich_text": [{"text": {"content": izoh}}]}

    mavjud = await grafik_topish(guruh_id, sana)
    if mavjud:
        await notion_update_page(mavjud["id"], props)
        return mavjud["id"]
    else:
        yangi = await notion_create_page(DB_GRAFIK, props)
        return yangi["id"]


async def davomat_yozuvlari(guruh_id: str, sana: str):
    """Shu guruh+sana uchun mavjud davomat yozuvlarini qaytaradi."""
    return await notion_query(
        DB_DAVOMAT,
        {
            "and": [
                {"property": "🚪 Guruhlar", "relation": {"contains": guruh_id}},
                {"property": "Date", "date": {"equals": sana}},
            ]
        },
    )


async def davomat_bormi(guruh_id: str, sana: str):
    """Shu guruh+sana uchun davomat allaqachon kiritilganmi?"""
    return len(await davomat_yozuvlari(guruh_id, sana))


async def eski_davomatni_ochir(guruh_id: str, sana: str):
    """Shu guruh+sana uchun eski davomat yozuvlarini arxivlaydi."""
    yozuvlar = await davomat_yozuvlari(guruh_id, sana)
    ochirildi = 0
    for y in yozuvlar:
        try:
            await notion_archive_page(y["id"])
            ochirildi += 1
        except Exception as e:
            log.warning(f"Arxivlashda xato ({y['id']}): {e}")
    return ochirildi


# ─────────────────────────────────────────────
#  BOT BUYRUQLARI
# ─────────────────────────────────────────────

MENYU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📋 Davomat")],
        [KeyboardButton("🚫 Dars qoldirish"), KeyboardButton("📅 Bugungi darslar")],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botni ishga tushirish yoki ro'yxatdan o'tish."""
    user = update.effective_user
    ustoz = await ustozni_top(user.id)

    if ustoz:
        ism = title_matn(ustoz, "Ustoz ismi")
        await update.message.reply_text(
            f"Assalomu alaykum, {ism}!\n\n"
            f"Quyidagi tugmalardan foydalaning 👇",
            reply_markup=MENYU,
        )
        context.user_data.pop("ism_kutilyapti", None)
        return

    # Yangi foydalanuvchi - ism so'raymiz
    context.user_data["ism_kutilyapti"] = True
    await update.message.reply_text(
        f"Assalomu alaykum!\n\n"
        f"Siz hali ro'yxatda yo'qsiz.\n\n"
        f"📝 *Markazdagi ismingizni yozing*\n"
        f"_(Notiondagi kabi, masalan: Ummu Aisha)_",
        parse_mode="Markdown",
    )


async def tekshir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnostika: bot qaysi bazalarni ko'ryapti?"""
    kutish = await update.message.reply_text("⏳ Tekshirilmoqda...")

    # Admin holati
    kim = update.effective_user.id
    admin_holat = ""
    if ADMIN_ID == 0:
        admin_holat = "⚠️ ADMIN_ID sozlanmagan (Railway Variables)\n\n"
    elif kim == ADMIN_ID:
        admin_holat = "✅ Siz adminsiz. So'rovlar shu chatga keladi.\n\n"
    else:
        admin_holat = (
            f"ℹ️ Siz admin emassiz.\n"
            f"Sizning ID: `{kim}`\n"
            f"Admin ID: `{ADMIN_ID}`\n\n"
        )

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

    satrlar = [admin_holat + f"👁 Bot {len(korinadigan)} ta bazani ko'ryapti:\n"]

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
    context.user_data.pop("qayta", None)
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

        # Guruhlarni xotirada saqlaymiz (ro'yxat sifatida, indeks bo'yicha)
        context.user_data["guruhlar"] = guruhlar

        tugmalar = [
            [InlineKeyboardButton(
                title_matn(g, "Guruh nomi"),
                callback_data=f"g:{i}"
            )]
            for i, g in enumerate(guruhlar)
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
    guruhlar = context.user_data.get("guruhlar")

    if not guruhlar:
        await q.edit_message_text("⚠️ Sessiya eskirdi. /davomat ni qayta yuboring.")
        return

    try:
        guruh = guruhlar[int(qisqa_id)]
    except (ValueError, IndexError):
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
    """Sana tanlandi -> takrorni tekshirib, talabalar ro'yxatini chiqaradi."""
    q = update.callback_query
    await q.answer()

    sana_str = q.data.split(":", 1)[1]
    context.user_data["sana"] = sana_str

    guruh = context.user_data.get("guruh")
    if not guruh:
        await q.edit_message_text("⚠️ Sessiya eskirdi. /davomat ni qayta yuboring.")
        return

    await q.edit_message_text("⏳ Tekshirilmoqda...")

    # Takror himoyasi
    try:
        soni = await davomat_bormi(guruh["id"], sana_str)
    except Exception as e:
        log.warning(f"davomat_bormi xatosi: {e}")
        soni = 0

    if soni:
        tugmalar = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Qayta kiritish", callback_data="qayta")],
            [InlineKeyboardButton("❌ Bekor", callback_data="bekor")],
        ])
        await q.edit_message_text(
            f"⚠️ *Bu kun davomati allaqachon kiritilgan*\n\n"
            f"📚 {title_matn(guruh, 'Guruh nomi')}\n"
            f"📅 {sana_matni(date.fromisoformat(sana_str))}\n"
            f"📝 Notionда {soni} ta yozuv bor\n\n"
            f"_Qayta kiritsangiz, eski {soni} ta yozuv o'chiriladi "
            f"va yangisi yoziladi._",
            parse_mode="Markdown",
            reply_markup=tugmalar,
        )
        return

    await talabalarni_yuklash_q(q, context)


async def talabalarni_yuklash_q(q, context):
    """Talabalar ro'yxatini yuklab chizadi (callback query orqali)."""
    guruh = context.user_data["guruh"]
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
                f"📚 Guruh: *{title_matn(guruh, 'Guruh nomi')}*\n\n"
                f"🔍 *Tashxis:*\n"
                f"Jami to'lovlar: {debug.get('jami_tolov', 0)} ta\n"
                f"Faol to'lovlar: {debug.get('tolov_soni', 0)} ta\n\n"
                f"Faoliyat holatlari:\n{faoliyat_matn}\n\n"
                f"_Bot «O'qiyabdi» va «Ta'til berildi» larni oladi._",
                parse_mode="Markdown",
            )
            return

        # Boshlanishida hammasi "Keldi"
        context.user_data["holatlar"] = {
            t["id"]: ("Tatilda" if t.get("tatilda") else "Keldi") for t in talabalar
        }
        context.user_data["talabalar"] = talabalar

        await royxatni_chiz(q, context)

    except Exception as e:
        log.exception("talabalarni_yuklash_q xatosi")
        await q.edit_message_text(f"⚠️ Xatolik:\n{e}")


BELGILAR = {"Keldi": "✅", "Kelmadi": "❌", "Tatilda": "🌙"}
KEYINGI = {"Keldi": "Kelmadi", "Kelmadi": "Tatilda", "Tatilda": "Keldi"}


async def royxatni_chiz(q, context):
    """Talabalar ro'yxatini tugmalar bilan chizadi (callback query orqali)."""
    await royxatni_chiz_xabar(q, context)


async def royxatni_chiz_xabar(xabar_yoki_q, context):
    """Talabalar ro'yxatini chizadi. xabar_yoki_q: Message yoki CallbackQuery."""
    talabalar = context.user_data["talabalar"]
    holatlar = context.user_data["holatlar"]
    guruh = context.user_data["guruh"]
    sana = date.fromisoformat(context.user_data["sana"])

    tugmalar = []
    tatil_boshlandi = False
    for i, t in enumerate(talabalar):
        holat = holatlar[t["id"]]
        belgi = BELGILAR[holat]

        # Ta'tildagilar bo'limi boshlanganda ajratuvchi qo'yamiz
        if t.get("tatilda") and not tatil_boshlandi:
            tatil_boshlandi = True
            tugmalar.append([
                InlineKeyboardButton("— — — ta'tildagilar — — —", callback_data="hech")
            ])

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
        f"📅 {sana_matni(sana)}  🕐 {guruh_vaqti(guruh)}\n\n"
        f"Talaba ismini bosing:\n"
        f"✅ Keldi → ❌ Kelmadi → 🌙 Ta'tilda\n\n"
        f"✅ {keldi}  ❌ {kelmadi}  🌙 {tatil}"
    )
    if hasattr(xabar_yoki_q, "edit_message_text"):
        await xabar_yoki_q.edit_message_text(
            matn, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(tugmalar),
        )
    else:
        await xabar_yoki_q.edit_text(
            matn, parse_mode="Markdown",
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


async def hech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ajratuvchi tugma - hech narsa qilmaydi."""
    await update.callback_query.answer()


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
        qayta = context.user_data.get("qayta", False)
        ustoz = await ustozni_top(q.from_user.id)

        # Qayta kiritish bo'lsa - eski yozuvlarni o'chiramiz
        ochirildi = 0
        if qayta:
            await q.edit_message_text("⏳ Eski yozuvlar o'chirilmoqda...")
            ochirildi = await eski_davomatni_ochir(guruh["id"], sana)
            await q.edit_message_text("⏳ Notionga saqlanmoqda...")

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

        # Grafikka "Dars boldi" yozamiz
        try:
            await grafik_yozish(guruh, ustoz, sana, "Dars boldi")
        except Exception as e:
            log.warning(f"Grafikka yozishda xato: {e}")

        keldi = sum(1 for h in holatlar.values() if h == "Keldi")
        kelmadi = sum(1 for h in holatlar.values() if h == "Kelmadi")
        tatil = sum(1 for h in holatlar.values() if h == "Tatilda")

        ochirish_matn = ""
        if ochirildi:
            ochirish_matn = f"🗑 Eski {ochirildi} ta yozuv o'chirildi\n"

        await q.edit_message_text(
            f"✅ *Saqlandi!*\n\n"
            f"📚 {title_matn(guruh, 'Guruh nomi')}\n"
            f"📅 {sana_matni(date.fromisoformat(sana))}\n\n"
            f"✅ Keldi: {keldi}\n"
            f"❌ Kelmadi: {kelmadi}\n"
            f"🌙 Ta'tilda: {tatil}\n\n"
            f"{ochirish_matn}"
            f"Jami {yozildi} ta yozuv Notionga yozildi.",
            parse_mode="Markdown",
        )
        for k in ["talabalar", "holatlar", "guruh", "sana", "qayta"]:
            context.user_data.pop(k, None)

    except Exception as e:
        log.exception("saqlash xatosi")
        await q.edit_message_text(f"⚠️ Saqlashda xatolik:\n{e}")


async def bekor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    for k in ["talabalar", "holatlar", "guruh", "sana", "qayta"]:
        context.user_data.pop(k, None)
    await q.edit_message_text("❌ Bekor qilindi.")


async def orqaga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    guruhlar = context.user_data.get("guruhlar", [])
    tugmalar = [
        [InlineKeyboardButton(
            title_matn(g, "Guruh nomi"), callback_data=f"g:{i}"
        )]
        for i, g in enumerate(guruhlar)
    ]
    await q.edit_message_text(
        "📚 Qaysi guruh?", reply_markup=InlineKeyboardMarkup(tugmalar)
    )


# ─────────────────────────────────────────────
#  BUGUNGI DARSLAR / ESLATMA
# ─────────────────────────────────────────────

# Yangi talaba sozlamalari
YANGI_KUN = 7      # to'lov yozuvi shu kun ichida yaratilgan bo'lsa
ESKI_KUN = 45      # shu guruhda oxirgi shuncha kunda boshqa to'lovi bo'lmasa -> yangi


async def guruh_yangi_talabalari(guruh_page, kun: date):
    """
    Shu guruhdagi YANGI talabalarni topadi.
    Shartlar:
      1. To'lov yozuvi oxirgi YANGI_KUN ichida yaratilgan
      2. Shu talabaning shu guruhda oxirgi ESKI_KUN ichida boshqa to'lovi yo'q
    Bu davr yangilanishini (eski talaba) tashlaydi,
    lekin qaytgan va butunlay yangi talabalarni ushlaydi.
    """
    guruh_id = guruh_page["id"]
    yangi_chegara = datetime.now(TZ) - timedelta(days=YANGI_KUN)
    eski_chegara = datetime.now(TZ) - timedelta(days=ESKI_KUN)

    # Shu guruhning BARCHA to'lovlari (holatidan qat'i nazar)
    hammasi = await notion_query(
        DB_TOLOVLAR,
        {"property": "Guruhlar", "relation": {"contains": guruh_id}},
    )

    # Talaba -> uning shu guruhdagi to'lovlari [(yaratilgan_vaqt, faoliyat)]
    talaba_tolovlari = {}
    for t in hammasi:
        rel = t["properties"].get("Toliba ismi", {}).get("relation", [])
        if not rel:
            continue
        tid = rel[0]["id"]
        yaratilgan = t.get("created_time")
        if not yaratilgan:
            continue
        try:
            vaqt = datetime.fromisoformat(yaratilgan.replace("Z", "+00:00"))
        except ValueError:
            continue
        talaba_tolovlari.setdefault(tid, []).append(vaqt)

    yangilar = []
    for tid, vaqtlar in talaba_tolovlari.items():
        vaqtlar.sort(reverse=True)
        oxirgi = vaqtlar[0]

        # 1-shart: oxirgi to'lov yaqinda yaratilganmi?
        if oxirgi < yangi_chegara:
            continue

        # 2-shart: undan oldin ESKI_KUN ichida boshqa to'lov bormi?
        eski_bor = any(v < oxirgi and v >= eski_chegara for v in vaqtlar[1:])
        if eski_bor:
            continue  # davr yangilanishi - yangi emas

        try:
            talaba = await notion_get_page(tid)
            yangilar.append(title_matn(talaba, "Name"))
        except Exception as e:
            log.warning(f"Yangi talaba o'qishda xato: {e}")

    return sorted(yangilar)


async def bugungi_darslar_matni(ustoz, kun: date):
    """Ustozning shu kundagi darslarini topib, matn va tugmalar qaytaradi."""
    guruhlar = await ustoz_guruhlari(ustoz)
    bugungi = [g for g in guruhlar if bugun_darsmi(g, kun)]

    if not bugungi:
        return None, None, []

    # Vaqt bo'yicha tartiblash
    bugungi.sort(key=lambda g: guruh_vaqti(g) or "99:99")

    satrlar = [f"🌅 *{sana_matni(kun)}*\n", f"Bugun sizda {len(bugungi)} ta dars bor:\n"]
    tugmalar = []

    for i, g in enumerate(bugungi):
        nom = title_matn(g, "Guruh nomi")
        vaqt = guruh_vaqti(g)
        satrlar.append(f"🕐 *{vaqt}* — {nom}")

        # Yangi talabalar bormi?
        try:
            yangilar = await guruh_yangi_talabalari(g, kun)
            for y in yangilar:
                satrlar.append(f"      🆕 Yangi: {y}")
        except Exception as e:
            log.warning(f"Yangi talaba tekshirishda xato: {e}")

        tugmalar.append([
            InlineKeyboardButton(f"📋 {vaqt} {nom}", callback_data=f"e:{i}")
        ])

    # Agar 1 tadan ko'p dars bo'lsa - "barchasini qoldirish" tugmasi
    if len(bugungi) > 1:
        tugmalar.append([
            InlineKeyboardButton(
                "🚫 Barcha darslarni qoldirish",
                callback_data="hammasi_qoldir"
            )
        ])

    satrlar.append("\n_Dars berib bo'lgach tugmani bosing._")

    return "\n".join(satrlar), InlineKeyboardMarkup(tugmalar), bugungi


async def bugungi_darslar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📅 Bugungi darslar tugmasi."""
    user = update.effective_user
    kutish = await update.message.reply_text("⏳ Yuklanmoqda...")

    ustoz = await ustozni_top(user.id)
    if not ustoz:
        await kutish.edit_text("❌ Siz ustozlar ro'yxatida topilmadingiz.")
        return

    kun = bugun()
    matn, tugmalar, guruhlar = await bugungi_darslar_matni(ustoz, kun)

    if not matn:
        await kutish.edit_text(f"😌 {sana_matni(kun)}\n\nBugun darsingiz yo'q.")
        return

    context.user_data["eslatma_guruhlar"] = guruhlar
    context.user_data["eslatma_sana"] = kun.isoformat()

    await kutish.edit_text(matn, parse_mode="Markdown", reply_markup=tugmalar)


async def tungi_eslatma(context: ContextTypes.DEFAULT_TYPE):
    """Har kuni 00:20 da barcha ustozlarga bugungi darslarni yuboradi."""
    kun = bugun()
    log.info(f"Tungi eslatma boshlandi: {kun}")

    try:
        ustozlar = await notion_query(DB_USTOZLAR)
    except Exception as e:
        log.exception("Eslatma: ustozlarni olishda xato")
        return

    yuborildi = 0
    for ustoz in ustozlar:
        tg_id = ustoz["properties"].get("Telegram ID", {}).get("number")
        if not tg_id:
            continue

        try:
            matn, tugmalar, guruhlar = await bugungi_darslar_matni(ustoz, kun)
            if not matn:
                continue

            # Har guruh uchun "Belgilanmagan" yozuv ochamiz
            for g in guruhlar:
                mavjud = await grafik_topish(g["id"], kun.isoformat())
                if not mavjud:
                    await grafik_yozish(g, ustoz, kun.isoformat(), "Belgilanmagan")

            await context.bot.send_message(
                chat_id=int(tg_id),
                text=matn,
                parse_mode="Markdown",
                reply_markup=tugmalar,
            )
            yuborildi += 1

        except Exception as e:
            log.warning(f"Eslatma yuborilmadi (tg_id={tg_id}): {e}")

    log.info(f"Tungi eslatma tugadi: {yuborildi} ta ustozga yuborildi")


async def hammasi_qoldir_sorov(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha darslarni qoldirish - tasdiq so'raydi."""
    q = update.callback_query
    await q.answer()

    guruhlar = context.user_data.get("eslatma_guruhlar")
    if not guruhlar:
        ustoz = await ustozni_top(q.from_user.id)
        if not ustoz:
            await q.message.reply_text("❌ Ustoz topilmadi.")
            return
        kun = bugun()
        _, _, guruhlar = await bugungi_darslar_matni(ustoz, kun)
        context.user_data["eslatma_guruhlar"] = guruhlar
        context.user_data["eslatma_sana"] = kun.isoformat()

    if not guruhlar:
        await q.message.reply_text("⚠️ Eslatma eskirdi. 📅 Bugungi darslar ni bosing.")
        return

    nomlar = "\n".join(
        f"   🚫 {title_matn(g, 'Guruh nomi')}" for g in guruhlar
    )

    tugmalar = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ha, hammasini qoldirish", callback_data="hammasi_ha")],
        [InlineKeyboardButton("❌ Yo'q", callback_data="hammasi_yoq")],
    ])

    await q.message.reply_text(
        f"⚠️ *Bugungi barcha darslarni qoldirasizmi?*\n\n"
        f"{nomlar}\n\n"
        f"_Keyin biror guruhga dars o'tsangiz, "
        f"davomat kiritganda u \"dars bo'ldi\" ga o'zgaradi._",
        parse_mode="Markdown",
        reply_markup=tugmalar,
    )


async def hammasi_qoldir_ha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tasdiqlandi - barcha bugungi darslarni qoldirildi qiladi."""
    q = update.callback_query
    await q.answer()

    guruhlar = context.user_data.get("eslatma_guruhlar")
    sana = context.user_data.get("eslatma_sana", bugun().isoformat())

    if not guruhlar:
        await q.edit_message_text("⚠️ Eslatma eskirdi.")
        return

    await q.edit_message_text("⏳ Saqlanmoqda...")

    ustoz = await ustozni_top(q.from_user.id)

    qoldirildi = 0
    otkazildi = 0
    otkazilganlar = []

    for g in guruhlar:
        # Bu guruhga davomat kiritilgan bo'lsa - tegmaymiz
        try:
            soni = await davomat_bormi(g["id"], sana)
        except Exception:
            soni = 0

        if soni:
            otkazildi += 1
            otkazilganlar.append(title_matn(g, "Guruh nomi"))
            continue

        try:
            await grafik_yozish(g, ustoz, sana, "Dars qoldirildi")
            qoldirildi += 1
        except Exception as e:
            log.warning(f"Hammasi qoldirishda xato: {e}")

    matn = (
        f"✅ *Bajarildi*\n\n"
        f"🚫 {qoldirildi} ta dars qoldirildi\n"
    )
    if otkazildi:
        nomlar = ", ".join(otkazilganlar)
        matn += (
            f"\n⏭ {otkazildi} ta guruhga tegilmadi "
            f"(davomat kiritilgan):\n_{nomlar}_"
        )

    await q.edit_message_text(matn, parse_mode="Markdown")


async def hammasi_qoldir_yoq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("❌ Bekor qilindi.")


async def eslatma_tugma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Eslatmadagi guruh tugmasi bosildi."""
    q = update.callback_query
    await q.answer()
    context.user_data.pop("qayta", None)

    i = int(q.data.split(":")[1])
    guruhlar = context.user_data.get("eslatma_guruhlar")

    # Eslatma eski bo'lsa, qaytadan yuklaymiz
    if not guruhlar:
        ustoz = await ustozni_top(q.from_user.id)
        if not ustoz:
            await q.message.reply_text("❌ Ustoz topilmadi.")
            return
        kun = bugun()
        _, _, guruhlar = await bugungi_darslar_matni(ustoz, kun)
        context.user_data["eslatma_guruhlar"] = guruhlar
        context.user_data["eslatma_sana"] = kun.isoformat()

    try:
        guruh = guruhlar[i]
    except (IndexError, TypeError):
        await q.message.reply_text("⚠️ Eslatma eskirdi. 📅 Bugungi darslar ni bosing.")
        return

    sana = context.user_data.get("eslatma_sana", bugun().isoformat())

    context.user_data["guruh"] = guruh
    context.user_data["sana"] = sana

    # Eslatma xabari joyida qolsin, yangi xabar ochamiz
    yangi = await q.message.reply_text("⏳ Tekshirilmoqda...")

    # Takror himoyasi
    try:
        soni = await davomat_bormi(guruh["id"], sana)
    except Exception as e:
        log.warning(f"davomat_bormi xatosi: {e}")
        soni = 0

    if soni:
        tugmalar = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Qayta kiritish", callback_data="qayta")],
            [InlineKeyboardButton("❌ Bekor", callback_data="bekor")],
        ])
        await yangi.edit_text(
            f"⚠️ *Bu kun davomati allaqachon kiritilgan*\n\n"
            f"📚 {title_matn(guruh, 'Guruh nomi')}\n"
            f"📅 {sana_matni(date.fromisoformat(sana))}\n"
            f"📝 Notionда {soni} ta yozuv bor\n\n"
            f"_Qayta kiritsangiz, eski {soni} ta yozuv o'chiriladi "
            f"va yangisi yoziladi._",
            parse_mode="Markdown",
            reply_markup=tugmalar,
        )
        return

    await talabalarni_yuklash_xabar(yangi, context)


async def talabalarni_yuklash_xabar(xabar, context):
    """Talabalar ro'yxatini yuklab chizadi (Message obyekti orqali)."""
    guruh = context.user_data["guruh"]
    await xabar.edit_text("⏳ Talabalar yuklanmoqda...")

    try:
        debug = {}
        talabalar = await guruh_talabalari(guruh, debug=debug)
        if not talabalar:
            await xabar.edit_text(
                f"❌ Faol talaba topilmadi.\n\n"
                f"📚 {title_matn(guruh, 'Guruh nomi')}\n"
                f"Jami to'lovlar: {debug.get('jami_tolov', 0)} ta\n"
                f"Faol: {debug.get('tolov_soni', 0)} ta"
            )
            return

        context.user_data["holatlar"] = {
            t["id"]: ("Tatilda" if t.get("tatilda") else "Keldi") for t in talabalar
        }
        context.user_data["talabalar"] = talabalar

        await royxatni_chiz_xabar(xabar, context)

    except Exception as e:
        log.exception("talabalarni_yuklash_xabar xatosi")
        await xabar.edit_text(f"⚠️ Xatolik:\n{e}")


async def qayta_kiritish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🔄 Qayta kiritish tugmasi - eski yozuvlar saqlashda o'chiriladi."""
    q = update.callback_query
    await q.answer()
    context.user_data["qayta"] = True
    await talabalarni_yuklash_q(q, context)


# ─────────────────────────────────────────────
#  DARS QOLDIRISH
# ─────────────────────────────────────────────

SABABLAR = ["Kasallik", "Sayohat", "Oilaviy sabab", "Texnik muammo", "Boshqa"]


async def dars_qoldirish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🚫 Dars qoldirish tugmasi - guruhlarni ko'rsatadi."""
    user = update.effective_user
    kutish = await update.message.reply_text("⏳ Yuklanmoqda...")

    ustoz = await ustozni_top(user.id)
    if not ustoz:
        await kutish.edit_text("❌ Siz ustozlar ro'yxatida topilmadingiz.")
        return

    guruhlar = await ustoz_guruhlari(ustoz)
    if not guruhlar:
        await kutish.edit_text("❌ Faol guruh topilmadi.")
        return

    context.user_data["guruhlar"] = guruhlar

    tugmalar = [
        [InlineKeyboardButton(title_matn(g, "Guruh nomi"), callback_data=f"q:{i}")]
        for i, g in enumerate(guruhlar)
    ]

    await kutish.edit_text(
        "🚫 *Dars qoldirish*\n\nQaysi guruh?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(tugmalar),
    )


async def qoldirish_guruh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dars qoldirish uchun guruh tanlandi -> sanani so'raydi."""
    q = update.callback_query
    await q.answer()

    i = int(q.data.split(":")[1])
    guruhlar = context.user_data.get("guruhlar")
    if not guruhlar:
        await q.edit_message_text("⚠️ Sessiya eskirdi.")
        return

    guruh = guruhlar[i]
    context.user_data["q_guruh"] = guruh

    # Yaqin dars kunlari (bugundan oldinga ham, orqaga ham)
    sanalar = []
    kun = bugun()
    for i in range(-7, 8):  # 1 hafta orqaga, 1 hafta oldinga
        k = kun + timedelta(days=i)
        if bugun_darsmi(guruh, k):
            sanalar.append(k)
    sanalar.sort(key=lambda d: abs((d - kun).days))
    sanalar = sanalar[:5]
    sanalar.sort()

    if not sanalar:
        sanalar = [kun]

    context.user_data["q_sanalar"] = sanalar

    tugmalar = []
    for i, d in enumerate(sanalar):
        belgi = "▪️"
        if d == kun:
            belgi = "📌"
        elif d > kun:
            belgi = "🔜"
        tugmalar.append([
            InlineKeyboardButton(f"{belgi} {sana_matni(d)}", callback_data=f"qs:{i}")
        ])

    await q.edit_message_text(
        f"🚫 *{title_matn(guruh, 'Guruh nomi')}*\n\nQaysi kun darsi qoldiriladi?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(tugmalar),
    )


async def qoldirish_sana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sana tanlandi -> sababni so'raydi."""
    q = update.callback_query
    await q.answer()

    i = int(q.data.split(":")[1])
    sanalar = context.user_data.get("q_sanalar")
    if not sanalar:
        await q.edit_message_text("⚠️ Sessiya eskirdi.")
        return

    d = sanalar[i]
    context.user_data["q_sana"] = d.isoformat()

    tugmalar = [
        [InlineKeyboardButton(s, callback_data=f"qb:{j}")]
        for j, s in enumerate(SABABLAR)
    ]

    guruh = context.user_data["q_guruh"]
    await q.edit_message_text(
        f"🚫 *{title_matn(guruh, 'Guruh nomi')}*\n"
        f"📅 {sana_matni(d)}\n\n"
        f"Sabab nima?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(tugmalar),
    )


async def qoldirish_sabab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sabab tanlandi -> Notionga yozadi."""
    q = update.callback_query
    await q.answer()

    j = int(q.data.split(":")[1])
    sabab = SABABLAR[j]

    guruh = context.user_data.get("q_guruh")
    sana = context.user_data.get("q_sana")
    if not guruh or not sana:
        await q.edit_message_text("⚠️ Sessiya eskirdi.")
        return

    await q.edit_message_text("⏳ Saqlanmoqda...")

    try:
        ustoz = await ustozni_top(q.from_user.id)
        await grafik_yozish(guruh, ustoz, sana, "Dars qoldirildi", sabab=sabab)

        await q.edit_message_text(
            f"✅ *Qayd etildi*\n\n"
            f"📚 {title_matn(guruh, 'Guruh nomi')}\n"
            f"📅 {sana_matni(date.fromisoformat(sana))}\n"
            f"🚫 Dars qoldirildi\n"
            f"📝 Sabab: {sabab}",
            parse_mode="Markdown",
        )
        context.user_data.pop("q_guruh", None)
        context.user_data.pop("q_sana", None)

    except Exception as e:
        log.exception("qoldirish_sabab xatosi")
        await q.edit_message_text(f"⚠️ Xatolik:\n{e}")


# ─────────────────────────────────────────────
#  RO'YXATDAN O'TISH
# ─────────────────────────────────────────────

# Kutilayotgan so'rovlar: {so'rov_id: {tg_id, ism, username, ustoz_id}}
SOROVLAR = {}


async def ustozni_ism_bilan_top(ism: str):
    """Ustozlar bazasidan ism bo'yicha qidiradi (taxminiy moslik)."""
    hammasi = await notion_query(DB_USTOZLAR)
    ism_toza = ism.strip().lower()

    aniq = []
    taxminiy = []

    for u in hammasi:
        u_ism = title_matn(u, "Ustoz ismi")
        u_toza = u_ism.strip().lower()

        if u_toza == ism_toza:
            aniq.append(u)
        elif ism_toza in u_toza or u_toza in ism_toza:
            taxminiy.append(u)

    return aniq if aniq else taxminiy


async def ism_qabul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yangi ustoz ismini yozdi -> adminga so'rov yuboradi."""
    user = update.effective_user
    ism = update.message.text.strip()

    # Allaqachon ro'yxatdami?
    mavjud = await ustozni_top(user.id)
    if mavjud:
        await update.message.reply_text(
            f"✅ Siz allaqachon ro'yxatdasiz.", reply_markup=MENYU
        )
        context.user_data.pop("ism_kutilyapti", None)
        return

    if len(ism) < 3:
        await update.message.reply_text(
            "❌ Ism juda qisqa. To'liq ismingizni yozing."
        )
        return

    kutish = await update.message.reply_text("⏳ Tekshirilmoqda...")

    # Notiondan qidiramiz
    try:
        topilgan = await ustozni_ism_bilan_top(ism)
    except Exception as e:
        log.exception("ism qidirishda xato")
        topilgan = []

    context.user_data.pop("ism_kutilyapti", None)

    # So'rovni saqlaymiz
    sorov_id = str(user.id)
    SOROVLAR[sorov_id] = {
        "tg_id": user.id,
        "ism": ism,
        "username": user.username or "—",
        "ustoz_id": topilgan[0]["id"] if len(topilgan) == 1 else None,
        "ustoz_ism": title_matn(topilgan[0], "Ustoz ismi") if len(topilgan) == 1 else None,
    }

    await kutish.edit_text(
        f"✅ *So'rovingiz yuborildi*\n\n"
        f"👤 {md_himoya(ism)}\n\n"
        f"Admin tasdiqlagach xabar beramiz.",
        parse_mode="Markdown",
    )

    # Adminga yuboramiz
    if not ADMIN_ID:
        log.warning("ADMIN_ID sozlanmagan!")
        return

    if len(topilgan) == 1:
        holat = f"✅ Notionda topildi: *{md_himoya(title_matn(topilgan[0], 'Ustoz ismi'))}*"
        tugmalar = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"ok:{sorov_id}")],
            [InlineKeyboardButton("❌ Rad etish", callback_data=f"no:{sorov_id}")],
        ])
    elif len(topilgan) > 1:
        nomlar = ", ".join(md_himoya(title_matn(u, "Ustoz ismi")) for u in topilgan[:5])
        holat = f"⚠️ Notionda {len(topilgan)} ta o'xshash:\n_{nomlar}_\n\nQo'lda tanlang:"
        tugmalar = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(
                    f"✅ {title_matn(u, 'Ustoz ismi')}",
                    callback_data=f"ok:{sorov_id}:{i}"
                )]
                for i, u in enumerate(topilgan[:5])
            ]
            + [[InlineKeyboardButton("❌ Rad etish", callback_data=f"no:{sorov_id}")]]
        )
        SOROVLAR[sorov_id]["variantlar"] = [
            {"id": u["id"], "ism": title_matn(u, "Ustoz ismi")} for u in topilgan[:5]
        ]
    else:
        holat = "❌ Notionda bunday ism topilmadi"
        tugmalar = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Rad etish", callback_data=f"no:{sorov_id}")],
        ])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"👤 *Yangi ustoz so'rovi*\n\n"
                f"Yozgan ismi: *{md_himoya(ism)}*\n"
                f"Telegram: @{user.username or '—'}\n"
                f"ID: `{user.id}`\n\n"
                f"{holat}"
            ),
            parse_mode="Markdown",
            reply_markup=tugmalar,
        )
    except Exception as e:
        log.warning(f"Adminga Markdown bilan yuborishda xato: {e}")
        # Markdown buzilgan bo'lishi mumkin - oddiy matn bilan qayta urinamiz
        try:
            holat_toza = (
                holat.replace("*", "").replace("_", "").replace("`", "")
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"👤 Yangi ustoz so'rovi\n\n"
                    f"Yozgan ismi: {ism}\n"
                    f"Telegram: @{user.username or '—'}\n"
                    f"ID: {user.id}\n\n"
                    f"{holat_toza}"
                ),
                reply_markup=tugmalar,
            )
        except Exception as e2:
            log.exception(f"Adminga oddiy matn bilan ham yuborilmadi: {e2}")


async def sorov_tasdiq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin tasdiqladi -> Notionga Telegram ID yoziladi."""
    q = update.callback_query

    if q.from_user.id != ADMIN_ID:
        await q.answer("Sizda ruxsat yo'q", show_alert=True)
        return

    await q.answer()

    qismlar = q.data.split(":")
    sorov_id = qismlar[1]
    variant_i = int(qismlar[2]) if len(qismlar) > 2 else None

    sorov = SOROVLAR.get(sorov_id)
    if not sorov:
        await q.edit_message_text(
            "⚠️ *So'rov eskirdi*\n\n"
            "Bot qayta ishga tushgan bo'lishi mumkin.\n"
            f"Ustozdan /start ni qayta bosishini so'rang.\n\n"
            f"Yoki ID ni qo'lda yozing: `{sorov_id}`",
            parse_mode="Markdown",
        )
        return

    # Qaysi ustoz?
    if variant_i is not None:
        variant = sorov["variantlar"][variant_i]
        ustoz_id = variant["id"]
        ustoz_ism = variant["ism"]
    else:
        ustoz_id = sorov["ustoz_id"]
        ustoz_ism = sorov["ustoz_ism"]

    if not ustoz_id:
        await q.edit_message_text("⚠️ Ustoz tanlanmagan.")
        return

    await q.edit_message_text("⏳ Notionga yozilmoqda...")

    try:
        tg_id = sorov["tg_id"]

        # Bu Telegram ID boshqa ustoz(lar)da bo'lsa - tozalaymiz
        eskilar = await notion_query(
            DB_USTOZLAR,
            {"property": "Telegram ID", "number": {"equals": tg_id}},
        )
        tozalandi = 0
        for e in eskilar:
            if e["id"] != ustoz_id:
                await notion_update_page(
                    e["id"], {"Telegram ID": {"number": None}}
                )
                tozalandi += 1

        # Yangi ustozga yozamiz
        await notion_update_page(
            ustoz_id,
            {"Telegram ID": {"number": tg_id}},
        )

        qoshimcha = ""
        if tozalandi:
            qoshimcha = f"\n🧹 {tozalandi} ta eski yozuvdan tozalandi"

        await q.edit_message_text(
            f"✅ *Tasdiqlandi*\n\n"
            f"👤 {ustoz_ism}\n"
            f"ID: `{tg_id}`\n\n"
            f"Notionga yozildi.{qoshimcha}",
            parse_mode="Markdown",
        )

        # Ustozga xabar
        try:
            await context.bot.send_message(
                chat_id=sorov["tg_id"],
                text=(
                    f"✅ Tasdiqlandi!\n\n"
                    f"Assalomu alaykum, {ustoz_ism}!\n"
                    f"Endi botdan foydalanishingiz mumkin 👇"
                ),
                reply_markup=MENYU,
            )
        except Exception as e:
            log.warning(f"Ustozga xabar yuborilmadi: {e}")

        SOROVLAR.pop(sorov_id, None)

    except Exception as e:
        log.exception("sorov_tasdiq xatosi")
        await q.edit_message_text(f"⚠️ Xatolik:\n{e}")


async def sorov_rad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin rad etdi."""
    q = update.callback_query

    if q.from_user.id != ADMIN_ID:
        await q.answer("Sizda ruxsat yo'q", show_alert=True)
        return

    await q.answer()

    sorov_id = q.data.split(":")[1]
    sorov = SOROVLAR.get(sorov_id)

    if sorov:
        try:
            await context.bot.send_message(
                chat_id=sorov["tg_id"],
                text=(
                    "❌ So'rovingiz rad etildi.\n\n"
                    "Savol bo'lsa markaz ma'muriyatiga murojaat qiling."
                ),
            )
        except Exception:
            pass
        SOROVLAR.pop(sorov_id, None)

    await q.edit_message_text("❌ Rad etildi.")


# ─────────────────────────────────────────────
#  MENYU TUGMALARI
# ─────────────────────────────────────────────

async def menyu_matn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menyu tugmalarini ushlaydi."""
    matn = update.message.text

    if matn == "📋 Davomat":
        await davomat(update, context)
    elif matn == "🚫 Dars qoldirish":
        await dars_qoldirish(update, context)
    elif matn == "📅 Bugungi darslar":
        await bugungi_darslar(update, context)


async def oddiy_matn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menyu tugmasi bo'lmagan matnlar."""
    # Ro'yxatda bormi?
    ustoz = await ustozni_top(update.effective_user.id)

    if ustoz:
        # Ro'yxatdagi ustoz - menyuni eslatamiz
        await update.message.reply_text(
            "Quyidagi tugmalardan foydalaning 👇", reply_markup=MENYU
        )
        return

    # Ro'yxatda YO'Q - har qanday matnni ism sifatida qabul qilamiz
    await ism_qabul(update, context)


# ─────────────────────────────────────────────
#  ISHGA TUSHIRISH
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("davomat", davomat))
    app.add_handler(CommandHandler("tekshir", tekshir))
    app.add_handler(CommandHandler("bugun", bugungi_darslar))

    # Menyu tugmalari
    app.add_handler(MessageHandler(
        filters.Regex("^(📋 Davomat|🚫 Dars qoldirish|📅 Bugungi darslar)$"),
        menyu_matn,
    ))

    # Ro'yxatdan o'tish (admin)
    app.add_handler(CallbackQueryHandler(sorov_tasdiq, pattern="^ok:"))
    app.add_handler(CallbackQueryHandler(sorov_rad, pattern="^no:"))

    # Davomat oqimi
    app.add_handler(CallbackQueryHandler(guruh_tanlandi, pattern="^g:"))
    app.add_handler(CallbackQueryHandler(sana_tanlandi, pattern="^s:"))
    app.add_handler(CallbackQueryHandler(talaba_bosildi, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(saqla, pattern="^saqla$"))
    app.add_handler(CallbackQueryHandler(bekor, pattern="^bekor$"))
    app.add_handler(CallbackQueryHandler(orqaga, pattern="^orqaga$"))
    app.add_handler(CallbackQueryHandler(hech, pattern="^hech$"))

    # Eslatma
    app.add_handler(CallbackQueryHandler(eslatma_tugma, pattern="^e:"))
    app.add_handler(CallbackQueryHandler(qayta_kiritish, pattern="^qayta$"))
    app.add_handler(CallbackQueryHandler(hammasi_qoldir_sorov, pattern="^hammasi_qoldir$"))
    app.add_handler(CallbackQueryHandler(hammasi_qoldir_ha, pattern="^hammasi_ha$"))
    app.add_handler(CallbackQueryHandler(hammasi_qoldir_yoq, pattern="^hammasi_yoq$"))

    # Dars qoldirish
    app.add_handler(CallbackQueryHandler(qoldirish_guruh, pattern="^q:"))
    app.add_handler(CallbackQueryHandler(qoldirish_sana, pattern="^qs:"))
    app.add_handler(CallbackQueryHandler(qoldirish_sabab, pattern="^qb:"))

    # Oddiy matn (ism qabul qilish) - eng oxirida bo'lishi kerak
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, oddiy_matn
    ))

    # Tungi eslatma - har kuni 00:20 (Toshkent)
    app.job_queue.run_daily(
        tungi_eslatma,
        time=dtime(hour=0, minute=20, tzinfo=TZ),
        name="tungi_eslatma",
    )

    log.info("Bot ishga tushdi ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
