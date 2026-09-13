# Model Usage & Extraction Report

## Summary
- **Target Task:** Filling missing `amount` values in `financial_events.csv` using OCR and Gemini Vision LLM.
- **Model Used:** `gemini-flash-lite-latest` (with local JSON caching layer).
- **Total Missing Records Target:** 16
- **Successfully Extracted:** 16
- **Final Null Count:** 0

## Pipeline Architecture
1. **Primary Pass:** Load `financial_events.csv` and `images.csv`.
2. **Cache Check:** Query local `image_cache.json` for cached results.
3. **LLM Extraction Fallback:** Call `gemini-flash-lite-latest` via structured prompt for any uncached images.
4. **Output Generation:** Update null values directly in memory and export to `dataset/output.csv`.