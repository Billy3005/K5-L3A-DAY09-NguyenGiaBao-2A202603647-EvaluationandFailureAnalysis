# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case của nhóm: `seeded_regression` C-002@11 (FM-03)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, report `E-seeded_regression-20261117-c38f` (eval set `c38fe0e1e779`). Cùng lỗi xuất hiện ở baseline: `swarm C-002@55` trong `E-swarm-20261117-c38f`, là dòng fail duy nhất của baseline |
| Triệu chứng | Fail `pass_rate` (`status resolved != denied`), `refund_amount_correct` (ledger 20000 ≠ nhãn 0), `db_fact_kept` (1 override event). Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." **khớp** ledger, nên đọc reply thì không thấy lỗi: ledger ghi một khoản hoàn mà chính sách không cho phép |
| Bước sai đầu tiên | event 3 · `model_override` · `{"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | event 1 `handoff` đúng hợp đồng (`allowed_tools` chỉ đọc, `principal_scope: owner_order_only`). Event 2 `investigator` đọc DB đúng: O-202 `delivered`, `delay_days: 2`, fee 20.000đ. Event 3 thay số 2 bằng số 5 lấy từ ticket ("giao trễ 5 ngày"). Event 4 `quote` 20.000đ (SHIP-LATE-03) chỉ là hệ quả: quote tính đúng trên một dữ kiện đã bị đổi |
| Failure mode | FM-03 ticket_trust (`docs/failure_taxonomy.md`, anchor A1) |
| Đối chứng | `fixed C-002@11`: event 3 là `quote {"amount_vnd": 0, "reason": "no_entitlement"}`, không có `model_override`, status `denied`, ledger 0. Grader Lab 8 PASS |
| Regression test | `src/tests/test_lab9_trace_case.py::test_lab9_trace_case_fm03_ticket_over_db` assert ba điều: event 3 là `model_override` sau `handoff`/`investigator`; `db_fact_kept`, `refund_amount_correct`, `pass_rate` đều fail trên `seeded_regression`; cả ba pass trên `fixed`. Test được chọn vào job CI (`-k "lab9 and not advanced"`). Đã chạy: pass |
| Test kèm (FM-08) | `test_lab9_trace_case_fm08_reply_states_unbooked_amount`: C-008@11 với agent misreport. Ledger 20.000đ, reply "35.000đ". `pass_rate` **pass** (grader Lab 8 không đọc reply), chỉ `reply_matches_ledger` fail. Trên `fixed` thì pass |
| Chưa chắc chắn | (1) Trace không ghi *vì sao* agent tin ticket. Trong `swarm`, lỗi chỉ xuất hiện ở seed chia hết cho 5 (từ code `variant_agent`, không thấy trên trace). Với LLM thật, tỉ lệ có thể khác. (2) Nếu agent đổi dữ kiện mà **không** ghi event (`quiet_override`), `db_fact_kept` không thấy. Probe cho thấy chỉ `refund_amount_correct`/`pass_rate` bắt được, nên test này không thay được grader mức kết quả. (3) Host không ghi event vào trace khi từ chối hay chấp nhận. Bằng chứng về tiền đến từ ledger, không từ trace |

Lệnh tái hiện:

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11
python -m lab9.trace_case --shape swarm --claim C-002 --seed 55          # cùng lỗi trong baseline
python -m pytest -q -m student -o addopts= -k trace_case
```

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
