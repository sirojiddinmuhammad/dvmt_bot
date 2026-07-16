# 📋 Davomat Bot — O'rnatish qo'llanmasi

## 1️⃣ Telegram bot yaratish

1. Telegramda **@BotFather** ni oching
2. `/newbot` yuboring
3. Bot nomi: `Annisaa Davomat`
4. Username: `annisaa_davomat_bot` (band bo'lsa boshqasi)
5. **Tokenni saqlang** — `7123456789:AAE...` ko'rinishida

---

## 2️⃣ GitHub'ga yuklash

1. [github.com](https://github.com) → **New repository**
2. Nomi: `davomat-bot`, **Private** tanlang
3. **Create repository**
4. **uploading an existing file** havolasini bosing
5. Quyidagi 5 ta faylni tashlang:
   - `bot.py`
   - `requirements.txt`
   - `Procfile`
   - `runtime.txt`
   - `.gitignore`
6. **Commit changes**

---

## 3️⃣ Railway'ga joylash

1. [railway.app](https://railway.app) → **Login with GitHub**
2. **New Project** → **Deploy from GitHub repo**
3. `davomat-bot` ni tanlang
4. **Variables** bo'limiga o'ting → **+ New Variable**:

| Nomi | Qiymati |
|------|---------|
| `BOT_TOKEN` | BotFather bergan token |
| `NOTION_TOKEN` | Notion integration token (`ntn_...`) |

5. **Deploy** — bot avtomatik ishga tushadi

✅ **Deploy Logs** da `Bot ishga tushdi ✅` yozuvi chiqsa — tayyor.

---

## 4️⃣ Ustozlarni ro'yxatga olish

Har bir ustoz:
1. Botga kiradi → `/start` bosadi
2. Bot ularga Telegram ID raqamini beradi
3. Ustoz raqamni sizga yuboradi
4. Siz Notion **🙂 Ustozlar** bazasiga `Telegram ID` ustuniga yozasiz

---

## 5️⃣ Talabalarni ro'yxatga olish

Yangi talaba qo'shganda `Telegram ID` ustunini to'ldiring.

**Talaba ID sini olish:** talaba botga `/start` bosadi → bot ID beradi → sizga yuboradi.

---

## 📱 Ustoz uchun yo'riqnoma

```
1. /davomat yozadi
2. Guruhni tanlaydi
3. Dars kunini tanlaydi
4. Talaba ismini bosadi:
   ✅ Keldi → ❌ Kelmadi → 🌙 Ta'tilda → ✅ Keldi ...
5. 💾 SAQLASH bosadi
```

Boshida hamma **✅ Keldi** turadi — faqat kelmaganlarni bosish kerak.

---

## ⚠️ Muhim eslatmalar

- **Tokenlarni hech kimga bermang** — faqat Railway Variables ga
- Guruh **"Guruh yopilgan"** statusida bo'lsa — ro'yxatda ko'rinmaydi
- Talaba **Faoliyat = "O'qiyabdi"** bo'lsagina ro'yxatga tushadi
- Guruhda **Dars kunlar** aniq belgilanmagan bo'lsa, bot oxirgi 4 kunni ko'rsatadi

---

## 🔧 Muammolar

| Muammo | Yechim |
|--------|--------|
| "Ustozlar ro'yxatida topilmadingiz" | Notionda Telegram ID yozilmagan |
| "Faol guruh topilmadi" | Ustozga guruh biriktirilmagan yoki hammasi yopilgan |
| "Bu guruhda faol talaba topilmadi" | To'lovlarda `Faoliyat = O'qiyabdi` yo'q |
| Bot javob bermayapti | Railway → Deploy Logs ni tekshiring |
| `object_not_found` xatosi | Integration bazaga ulanmagan (Connections) |

---

## 📊 Notionda ko'rish

**Davomat jadvali** bazasida:
- **Group by** → `🚪 Guruhlar` — guruh bo'yicha
- **Filter** → `Holat = Kelmadi` — kelmaganlar
- **Filter** → `Date = Past week` — shu hafta

**Kim ko'p qoldirdi?** → Filter: `Holat = Kelmadi` + Group by: `🎓 Tolibalar`
