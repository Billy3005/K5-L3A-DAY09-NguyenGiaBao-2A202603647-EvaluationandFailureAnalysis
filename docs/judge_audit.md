# Judge audit

Chạy (trong `src/`, backend REPLAY, không cần key):

```bash
python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10      # exit 0
python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 30   # exit 1
```

Lớp dương là `FAIL` (việc của judge là *tìm* reply hỏng). Ngưỡng FLOORS: κ ≥ 0.6, P ≥ 0.8, R ≥ 0.7. κ không xác định (judge trả một nhãn hằng) ⇒ FAIL.

| Tiêu chí | n | κ | Precision | Recall | Ma trận (tp/fp/fn/tn) | Tỉ lệ FAIL quan sát → hiệu chỉnh (Rogan-Gladen) | Đạt FLOORS? | Quyết định |
|---|---|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | 24 / 4 / 6 / 66 | 0.28 → 0.30 | **Đạt** | **dùng** (non-blocking, báo cáo kèm tỉ lệ đã hiệu chỉnh) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | 9 / 0 / 21 / 70 | 0.09 → 0.30 | **Không** (κ, R) | **loại**, không dùng làm gate |

Vì sao loại `helpful` dù precision = 1.0: judge này chỉ bắt được 9/30 reply mà người chấm cho là hỏng. Nó báo tỉ lệ lỗi 9%, trong khi tỉ lệ thật sau hiệu chỉnh là 30%. Một gate dùng judge này sẽ cho qua khoảng 70% reply hỏng, nên chỉ trông có vẻ an toàn. Precision cao chỉ có nghĩa là khi judge đã nói FAIL thì nó đúng, chứ không có nghĩa là nó tìm ra được lỗi.

Quan trọng hơn, lỗi của `helpful` có quy luật. 18/21 ca judge bỏ sót có nhãn người `human_failure = sai tiền` (ghi chú "ledger ghi X"), tức reply nêu **số tiền khác ledger**. Đây chính là FM-08. 3 ca còn lại là `sai xưng hô` (HL-047, HL-091, HL-097: "Ok bạn, … nhé"). *Giả thuyết* (CSV replay không lưu prompt hay lập luận của judge, nên chưa kiểm chứng được): judge không được cấp ledger và bị những reply dài dòng như "thành thật xin lỗi… rất trân trọng…" làm lệch. Dù nguyên nhân là gì, ma trận nhầm lẫn đã đủ để loại judge này. Vì vậy FM-08 được chấm bằng code (`reply_matches_ledger`, đối chiếu reply với ledger), không bằng judge.

## Audit thủ công 8 ca bất đồng

| # | Case | Judge nói | Người nói | Ai đúng? | Hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | tone H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | **Người** | Rubric tone thêm: "xưng *em*/*bạn* + tiểu từ *nha/nhé* với khách khiếu nại = FAIL". Thêm 2 ví dụ few-shot (H-008, H-017) |
| 2 | tone H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | **Người** | Judge chỉ dựa vào từ khóa lịch sự ("Dạ, vui lòng"). Rubric thêm: "câu mệnh lệnh chặn khách liên hệ (*không cần nhắn thêm*) = FAIL dù có kính ngữ" |
| 3 | tone H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | **Người** | Judge đang tự đặt thêm luật "phải xin lỗi". Rubric ghi rõ: "ngắn, đủ ý, có kính ngữ = PASS; không bắt buộc xin lỗi khi yêu cầu đã được đáp ứng" |
| 4 | tone H-051 "LogiX đã refund 90.000đ…" | FAIL | PASS | **Người** | Thêm danh sách từ mượn CS LogiX cho phép (*refund*, *ship*, *voucher*) vào rubric. Judge không được phạt các từ này |
| 5 | tone H-081 "đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** (ghi chú: người đọc nhầm ticket) | Sửa **nhãn người** thành FAIL trong bộ nhãn lần sau. Ghi lại để biết nhãn người cũng có nhiễu. Đây là lý do cần 2 người gán nhãn và đo κ giữa người với người |
| 6 | tone H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm…" | PASS | FAIL | **Người** | Đây không phải lỗi giọng điệu mà là hứa hẹn ngoài hệ thống. Tách khỏi tone: chuyển sang grader code "reply chỉ được nêu số tiền có trong ledger" (cùng họ với `reply_matches_ledger`). Rubric tone ghi rõ là không chấm nội dung tiền |
| 7 | helpful HL-015 "LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | **Người** | Nếu judge không được cấp ledger (giả thuyết, replay không lưu prompt) thì nó không thể chấm đúng. Không sửa prompt judge mà **bỏ tiêu chí tiền khỏi helpful**, để `reply_matches_ledger` (code) chấm. Đã có trong `metric_spec.yaml` |
| 8 | helpful HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." (nhãn người: `sai xưng hô`) | PASS | FAIL | **Người** | Xưng "bạn" + "nhé" với khách khiếu nại, và reply không nêu kết quả (hoàn bao nhiêu hay bị từ chối). Rubric helpful thêm: "phải nêu quyết định và số tiền (nếu có)". Xưng hô chuyển sang tiêu chí tone (đã calibrate). Muốn dùng lại judge này phải calibrate lại trên tập held-out mới |

Nhận xét chung: ở `tone`, 6/10 bất đồng là judge **bỏ sót** (người FAIL, judge PASS), gồm 2 xưng hô suồng sã (H-008, H-017), 1 giọng ra lệnh (H-029), 1 hứa hẹn ngoài hệ thống (H-063), 1 emoji (H-070), 1 mất dấu tiếng Việt (H-094). 3/10 là judge phạt quá tay (thiếu xin lỗi, từ mượn, lặp từ). 1/10 là nhãn người sai. Sửa rubric theo các hành động 1–4 nhắm vào chính các nhóm lỗi này.

## Kết luận

- **tone_ok: dùng được**, đạt cả ba FLOORS. Chỉ dùng làm metric theo dõi (non-blocking) và báo tỉ lệ FAIL **đã hiệu chỉnh** (0.30), không báo tỉ lệ thô (0.28).
- **helpful: loại**. κ = 0.375 < 0.6, R = 0.30 < 0.7 (ma trận 9/0/21/70). Judge bỏ sót có quy luật chính lỗi FM-08, đúng loại lỗi về tiền mà quy tắc của lab bắt chấm bằng code.
- Tiền và approval không bao giờ chấm bằng judge: `refund_amount_correct`, `reply_matches_ledger`, `approval_bypass` đều là grader code.
- Rủi ro còn lại: judge mới chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng mỗi tiêu chí), và có ít nhất 1 nhãn người sai (H-081). Cần nhãn người mới, do ≥2 người gán nhãn, để có tập held-out thật trước khi cho judge chặn release.
