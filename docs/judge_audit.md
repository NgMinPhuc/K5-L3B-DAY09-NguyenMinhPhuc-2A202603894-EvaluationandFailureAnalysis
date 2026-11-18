# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (từ `src/`, ví dụ `--csv data/eval/judge_tone_public.csv`).
Lớp dương = FAIL (judge có nhiệm vụ tìm lỗi). Floors: κ≥0.6, P≥0.8, R≥0.7.

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | PASS (cả 3 floor) | **dùng** |
| helpful | 100 | 0.375 | 1.000 | 0.300 | FAIL (κ và Recall) | **loại** (không dùng làm gate) |

Ma trận nhầm lẫn (FAIL = dương):
- tone_ok: tp=24, fp=4, fn=6, tn=66. Tỉ lệ FAIL quan sát 0.28 -> hiệu chỉnh Rogan-Gladen 0.30 (sens=0.8, spec=66/70≈0.943).
- helpful: tp=9, fp=0, fn=21, tn=70. Tỉ lệ FAIL quan sát 0.09 -> hiệu chỉnh 0.30 (sens=0.3, spec=1.0). Judge nhìn thấy chưa đến 1/3 lỗi thật.

## ≥5 case audit thủ công
Tone (5 bất đồng đầu của `--audit 5`; đã đọc cả dòng CSV):

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người: xưng "em", "nha" suồng sã, không có "Dạ" | Rubric thêm: bắt buộc "Dạ/ạ", cấm "nha/nhé/em" với khách; thêm ví dụ FAIL few-shot này |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ cho đơn O-606 rồi nha." | PASS | FAIL | Người: "bạn ơi", "nha" không phù hợp khách lớn tuổi, dù có "Dạ" | Rubric: chỉ cho phép đại từ "anh/chị/quý khách"; có "Dạ" không đủ bù đại từ sai |
| 3 | H-029 "Dạ, vui lòng chờ. Hệ thống đang xử lý, không cần nhắn thêm." | PASS | FAIL | Người: "không cần nhắn thêm" là giọng ra lệnh, từ chối khách | Rubric: liệt kê câu mệnh lệnh/cấm đoán là FAIL; judge phải đọc cả câu, không chỉ từ lễ phép mở đầu |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người: ngắn, lịch sự, đủ thông tin; đây không phải lỗi giọng | Rubric: ghi rõ "không bắt buộc xin lỗi khi yêu cầu thành công"; chỉ yêu cầu xin lỗi khi có sự cố |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ." | FAIL | PASS | Người: "refund" nằm trong từ vựng CS LogiX cho phép | Rubric: thêm whitelist thuật ngữ (refund, ...) , bỏ quy tắc "cấm tiếng Anh" chung chung |

Helpful (5 bất đồng đầu; toàn bộ là judge PASS nhưng người FAIL, tức fn):

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / hành động sửa rubric |
|---|---|---|---|---|---|
| 6 | HL-015 "...hoàn 55.000đ cho đơn O-112" | PASS | FAIL | Người: ledger ghi 40.000đ, sai tiền | Judge không có ledger nên không kiểm được số. Đưa `ledger_amount` vào prompt hoặc chuyển sang grader tất định so khớp số tiền (FM-08) |
| 7 | HL-016 (xin lỗi dài dòng, "hoàn 55.000đ cho đơn O-404") | PASS | FAIL | Người: ledger 35.000đ; lời xin lỗi dài che mất số sai | Judge bị lừa bởi độ dài/lịch sự (verbosity bias). Rubric: chấm tính đúng sự thật trước, bỏ qua độ dài |
| 8 | HL-020 "...hoàn 90.000đ cho đơn O-303" | PASS | FAIL | Người: ledger 120.000đ | Cùng gốc với #6: sai số tiền không thể thấy nếu thiếu ground truth |
| 9 | HL-028 (xin lỗi lặp 2 lần, "hoàn 50.000đ cho đơn O-677") | PASS | FAIL | Người: ledger 35.000đ, câu xin lỗi lặp là rác | Thêm tiêu chí phạt lặp ý; số tiền vẫn phải kiểm bằng ledger |
| 10 | HL-035 (xin lỗi lặp, "hoàn 50.000đ cho đơn O-303") | PASS | FAIL | Người: ledger 35.000đ | Như #9; few-shot FAIL có sai số tiền kèm văn phong tốt |

## Kết luận
- **tone_ok = dùng được**: κ 0.757 ≥ 0.6, P 0.857 ≥ 0.8, R 0.8 ≥ 0.7. Sai sót còn lại (fn=6, fp=4) đều là các quy tắc rubric mơ hồ (đại từ, "nha", xin lỗi, tiếng Anh) và sửa được bằng rubric ở #1-#5. Nên báo tỉ lệ FAIL hiệu chỉnh (0.30) thay vì tỉ lệ quan sát (0.28).
- **helpful = loại, không được dùng làm gate**: nó trượt hai floor là Recall 0.3 < 0.7 (bỏ sót 21/30 lỗi thật) và κ 0.375 < 0.6. Precision 1.0 không cứu được vì judge chỉ bắt các lỗi hiển nhiên (lạc đề, thiếu hỏi mã đơn). Hệ quả cho gate: (a) tỉ lệ FAIL quan sát 0.09 thấp hơn nhiều thực tế 0.30, nên một bản hồi quy thật (reply sai số tiền) vẫn PASS, tức gate bỏ sót regression; (b) κ thấp nghĩa là phần lớn "đồng thuận" chỉ do ngẫu nhiên nên điểm số dao động theo mẫu, không phản ánh chất lượng, gate thành nhiễu; (c) vì lỗi đều là fn, gate sẽ cho qua lỗi tiền bạc nghiêm trọng (FM-08) một cách im lặng. Nguyên nhân gốc là judge không thấy ledger và bị verbosity bias. Hành động: số tiền do grader tất định kiểm; rubric "helpful" cần ground truth và tách tiêu chí; hiệu chuẩn lại trước khi dùng.
- Rủi ro còn lại: đây là **judge replay** (verdict đọc sẵn từ CSV, không gọi LLM thật) nên không chứng minh judge thật sau khi sửa rubric sẽ tốt hơn; mẫu nhỏ (n=100, 30 FAIL/tiêu chí) nên khoảng tin cậy của κ/R rộng; cần nhãn người mới (held-out) để kiểm lại sau khi sửa rubric, tránh overfit vào đúng các case đã audit.
