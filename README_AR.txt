Glycol Watch v3 — Auto Daily Update
====================================

النسخة تحتوي على:
- تطبيق PWA للموبايل (index.html)
- ملف البيانات المركزي (prices.json)
- تحديث تلقائي عند فتح التطبيق
- سكربت update_prices.py لتحديث الأسعار من المصادر العامة
- GitHub Action يعمل يومياً
- حفظ آخر قيمة إذا تعذر مصدر معيّن

طريقة النشر المقترحة على GitHub Pages:
1) أنشئ Repository جديد وارفع كل محتويات هذا المجلد كما هي، بما فيها مجلد .github.
2) من Settings > Pages اختر Deploy from a branch ثم main / root.
3) من Settings > Actions تأكد أن Workflows مسموح لها بالعمل والكتابة.
4) افتح رابط GitHub Pages من Chrome في الهاتف واختر Install app / Add to Home screen.
5) ملف prices.json يتحدث يومياً بواسطة GitHub Action، والتطبيق يسحبه عند الفتح.

ملاحظة مهمة:
المصادر العامة قد تغيّر تصميم صفحاتها أو تحجب القراءة الآلية. لذلك السكربت لا يمسح القيمة القديمة عند فشل القراءة؛ يحتفظ بآخر قيمة ويستمر.
MEG = Contract Price مرجعي من MEGlobal.
DEG وTEG = مؤشرات سوق صينية.
PEG400 = Supplier Indication وليس Benchmark موحداً.
