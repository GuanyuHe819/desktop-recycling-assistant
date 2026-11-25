# Evaluation data

The two Keras models and their paired label files are in `models/`. The Dashboard defaults to the dataset baseline. Choose **My samples** in **Recognition model** to use the model trained with desktop examples.

The `Report_Presentation_Assets` folder holds summary metrics, confusion counts, confidence sweeps, measured inference times, and exclusions from the public comparison. The threshold sweep uses single images. Live decisions also require stability across eight frames and a ready controller. The supplemental and empty-scene replay cohorts reused material available during development. Their scores do not measure generalisation.

`draw_figures.py` takes the saved `metrics.json` as input. To rerun it on Windows, install ReportLab and make sure the Segoe UI fonts are available.

`evaluate_models.py` requires the [RealWaste originals](https://doi.org/10.24432/C5SS4G) and the original desktop and empty-scene captures. The public repository includes the exports and saved metrics and omits the private captures. To stage the public part of the evaluation:

1. Under `data/RealWaste_五类候选_未筛选/`, copy the RealWaste directories `Paper`, `Plastic`, `Metal`, `Cardboard`, and `Textile Trash` as `paper`, `plastic`, `metal`, `cardboard`, and `textile`. Preserve each image filename and content.
2. Use `data/TeachableMachine_五类_初筛/来源清单.csv` to recreate the five screened class folders under `data/TeachableMachine_五类_初筛/`. The script checks those files against the recorded hashes.
3. An exact rerun additionally requires the original private files under `data/补充/` and `data/empty_需要自行拍摄/`. Review the included metrics and exclusion list when these inputs are unavailable.
