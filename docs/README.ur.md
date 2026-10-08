<p align="center">
  <img src="../assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://github.com/Mftrferdinand/Zeline/tree/main/docs"><img src="https://img.shields.io/badge/Docs-zeline.zerolinear.com-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/Community-0A84FF?style=flat&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/License-MIT-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="../README.md"><img src="https://img.shields.io/badge/Lang-EN-0A84FF?style=flat&labelColor=334155"></a>
  <a href="README.id.md"><img src="https://img.shields.io/badge/Lang-ID-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/Lang-中文-0A84FF?style=flat&labelColor=334155"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-ES-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.ur.md"><img src="https://img.shields.io/badge/Lang-اردو-0A84FF?style=flat&labelColor=334155"></a>
  <br>
  <strong>Zeline Agentic AI</strong> — Zerolinear کی طرف سے، ایک AI تحقیقی لیب۔
</p>

---

# Zeline

Zeline **Zerolinear** کا تیار کردہ ایک اوپن سورس ایجنٹک AI فریم ورک ہے۔ یہ AI ایجنٹس بنانے کے لیے ایک لچکدار بنیاد ہے جو استدلال کر سکتے ہیں، ٹولز استعمال کر سکتے ہیں، بیرونی سسٹمز کے ساتھ تعامل کر سکتے ہیں، اور پیچیدہ کثیر مرحلہ ورک فلو انجام دے سکتے ہیں۔

کسی ایک ماڈل، فراہم کنندہ، یا انفراسٹرکچر سے منسلک ہونے کے بجائے، Zeline لچک کو ترجیح دے کر بنایا گیا ہے۔ اپنے پسندیدہ AI ماڈلز اور OpenAI ہم آہنگ اینڈ پوائنٹس کو جوڑیں، فراہم کنندگان ترتیب دیں، ٹولز ضم کریں، اور فریم ورک کو اپنے کام کے انداز کے مطابق بڑھائیں — ماڈل اور فراہم کنندگان کو سسٹم دوبارہ بنائے بغیر تبدیل کیا جا سکتا ہے۔

اسے ڈویلپمنٹ کے لیے مقامی طور پر چلائیں یا اپنے سرور یا کلاؤڈ پر تعینات کریں، اور اسے اپنے استعمال کردہ انٹرفیسز سے جوڑیں۔ مقصد کنٹرول ڈویلپر کے ہاتھ میں رکھنا ہے: آپ کے ماڈلز، آپ کے ٹولز، آپ کا انفراسٹرکچر، آپ کا ڈیٹا۔ اوپن سورس، ماڈل ایگناسٹک، توسیع پذیر۔

## خصوصیات

- **ایجنٹ کور** — OpenAI ہم آہنگ ایجنٹ لوپ مع ٹول کالنگ، انٹرایکٹو CLI
- **ماڈل ایگناسٹک** — OpenAI، OpenRouter، vLLM، Ollama اور کسی بھی ہم آہنگ API کے ساتھ کام کرتا ہے
- **API کی پول** — فی فراہم کنندہ متعدد کیز (`zeline keys add`)؛ ناکام کیز خودکار طور پر تبدیل
- **مستقل میموری** — پلیٹ فارم شناخت کے لحاظ سے الگ طویل مدتی میموری
- **FTS5 سیشن تلاش** — تمام ماضی کے سیشنز میں مکمل متن تلاش
- **خاموش پرامپٹ انجیکشن فلٹر** — بیرونی ڈیٹا میں بدنیتی ہدایات کا پتہ، صارف کو پریشان کیے بغیر
- **درجہ بند منظوری** — معمول کے آپریشنز خودکار؛ خطرناک آپریشنز کے لیے منظوری درکار
- **اسکلز** — دوبارہ قابل استعمال Markdown طریقہ کار
- **میسجنگ گیٹ ویز** — Telegram، Discord، WhatsApp
- **بلٹ ان ٹولز** — ویب تلاش، گہری تحقیق، فائل پڑھنا/لکھنا، کوڈ چلانا، شیل
- **براؤزر کنٹرول** — Chrome DevTools Protocol کے ذریعے حقیقی Chromium کنٹرول
- **شیڈولڈ جابز** — `zeline cron` وقفہ یا cron اظہار پر جابز چلاتا ہے
- **Human-in-the-loop** — `ask_user` ایک سوال کے لیے روکتا ہے
- **MCP کلائنٹ** — بیرونی MCP سرورز جوڑیں

## تنصیب

**تقاضے:** Python 3.10+۔ WhatsApp کے لیے Node.js 18+ اور npm بھی۔

### PyPI (تجویز کردہ)

```sh
pip install zeline
```

پھر `zeline setup`۔

### Linux، macOS، Termux

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.5/install.sh && bash install.sh
```

پھر `zeline setup`۔

### Windows PowerShell

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.5/install.ps1 -OutFile install.ps1; .\install.ps1
```

پھر `zeline setup`۔

## فوری آغاز

```sh
zeline setup       # انٹرایکٹو سیٹ اپ
zeline gateway     # گیٹ وے شروع کریں
zeline ask "جکارتہ میں کیا وقت ہے؟"
```

## سیکیورٹی

Zeline درجہ بند منظوری ماڈل استعمال کرتا ہے:
- **پڑھنا/لکھنا/نیٹ ورک**: خودکار اجازت، کوئی سوال نہیں
- **انسٹال/تباہ کن**: آپ کی منظوری درکار؛ جواب نہ ملے تو خودکار انکار
- **سیکرٹس**: کبھی لاگز یا چیٹ میں ظاہر نہیں ہوتے
- **انجیکشن فلٹر**: مشکوک ہدایات خاموشی سے نظر انداز

سب کچھ بغیر سوال کے اجازت کے لیے:
```json
// ~/.zeline/config.json
{"approval": {"auto_allow_all": true}}
```

## مکمل دستاویزات

مکمل دستاویزات انگریزی میں: [README.md](../README.md)

## لائسنس

MIT — [LICENSE](../LICENSE) دیکھیں۔
