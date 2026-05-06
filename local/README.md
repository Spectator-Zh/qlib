# Local Workflow

这个目录用于本地主板 + ETF 预测流程。

上传到 GitHub 时，代码应保留，`data/`、`results/`、缓存和导出文件不应提交。

## 推荐入口

1. `./methods/1_update_today_data.sh`
   用腾讯历史日 K 把本地日线补到最新交易日。
   如果本地已经是最新日，默认只提示，不覆盖最新日。

2. `./methods/2_rebuild_mainboard_etf_pool.sh`
   重建股票池 `instruments/mainboard_etf.txt`。
   同时重建 `data/cn_day_qlib/calendars/day.txt`，保证这套 3608 个标的与交易日历一致。

3. `./methods/3_run_train_mainboard_etf.sh`
   运行 LightGBM 版本训练。

3. `./methods/3_run_train_mainboard_etf_double.sh`
   运行 DoubleEnsemble 版本训练。

4. `./methods/4_annotate_latest_mainboard_etf.sh`
   给最新一次 `*_mainboard_etf*` 结果补后验涨跌幅，并生成 HTML 视图。

旧文件名仍然保留，可继续使用，但内部会转发到以上编号入口。

## 目录说明

- `scripts/`
  Python 逻辑。

- `methods/`
  直接可运行的 shell 入口。

- `data/`
  本地数据目录，不建议提交到 GitHub。

- `results/`
  训练和标注结果目录，不建议提交到 GitHub。

- `instruments/`
  股票池和元数据缓存目录。

## 结果文件

- `pred.csv`
  全量预测结果。测试区间内每个交易日、每个标的一行。

- `pred_head.csv`
  `pred.csv` 的前 200 行，只用于快速预览。

- `latest_day_all.csv`
  最新一个信号日的全量横截面。

- `latest_day_mainboard_top20.csv`
  最新一个信号日里，非 `ST/*ST` 主板 top/bottom 结果。

- `latest_day_etf_top20.csv`
  最新一个信号日里，ETF top/bottom 结果。

- `latest_day_st_top20.csv`
  最新一个信号日里，`ST/*ST` top/bottom 结果。

- 同名 `.html`
  对应 CSV 的可视化视图。

- `summary.json`
  某次运行的摘要信息。

## 当前榜单字段

- `datetime`
- `instrument`
- `score`
- `name`
- `prediction_date`
- `signal_close`
- `next_close`
- `actual_return_pct`
- `hit`

## 无数据时的行为

- 没有 `data/cn_day_csv` 时，更新脚本会直接提示缺少数据目录。
- 没有 `data/cn_day_qlib` 或交易日历时，训练脚本会直接提示缺少依赖文件。
- 没有 `results/` 或没有训练结果时，标注脚本会直接提示，而不是报路径错误。
