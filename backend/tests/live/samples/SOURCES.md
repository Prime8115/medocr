# Sources of the live-test samples

- `cord/` - 10 receipt photos from **CORD v2** (`naver-clova-ix/cord-v2`, test
  split; receipts with 3-8 items), by NAVER Clova, licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Resized to at most
  1600 px. `answers.json` holds their published answers (item names, counts,
  prices, totals). https://huggingface.co/datasets/naver-clova-ix/cord-v2
- `fonts/Kalam-Regular.ttf` - Kalam, by the Indian Type Foundry, and
  `fonts/Caveat.ttf` - Caveat, by Impallari Type; both from Google Fonts under
  the SIL Open Font License 1.1 (`fonts/OFL.txt`). Used to write the
  handwritten test bills (`photo_kit.py`).
