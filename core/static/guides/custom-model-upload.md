# Custom model: prepare the folder and upload it

This is the complete procedure for running your own compiled detection model
on the box. It takes two files. Nothing else is needed -- no Python script,
no extra config.

## 1. What a model is, to this system

A model is **two files with the same name**:

```
my_model/
  my_model.hef     the compiled network (from the Hailo Dataflow Compiler)
  my_model.json    what the network detects: the class names, in order
```

- `.hef` -- the weights, compiled for the chip in your box. The input size is
  read from inside this file; you do not declare it.
- `.json` -- the description. The runtime cannot know your class names from
  the weights, so this file supplies them.

`infer_example.py` or any other script that came with your compile is **not
used** and should not be uploaded.

## 2. The .json

Exactly this shape. Replace the values with your own:

```json
{
  "model": "my_model",
  "hef": "my_model.hef",
  "num_classes": 3,
  "classes": [
    "Sliding-Door",
    "pointer",
    "pointer_2"
  ],
  "target": "hailo10h"
}
```

| Key | Meaning | Rule |
|-----|---------|------|
| `model` | the model's name | same as the file names, without extension |
| `hef` | the weights file | `<model>.hef` |
| `num_classes` | how many classes | must equal the length of `classes` |
| `classes` | the class names | **in the model's output order** -- class 0 first. Same order as the dataset the model was trained on |
| `target` | the chip it was compiled for | `hailo10h`, `hailo8` or `hailo8l` -- must match the chip in the box |

Optional keys the system also reads if present: `confidence` (0-1, the
detection threshold to start with; the default is fine), `description`.

## 3. Naming rules

- The `.hef` and the `.json` **must share one name**: `my_model.hef` and
  `my_model.json`. A pair with different names is refused.
- Names: letters, digits, `_`, `-`, `.` only. No spaces.
- `target` must be the chip in your box. A model compiled for `hailo8` does
  not load on a `hailo10h` box (and the other way round). The upload checks
  this and tells you.

## 4. Upload from the dashboard

1. Open the system, then **Models** in the rail.
2. In the **Custom Models** card, press **+ Upload a model**.
3. Pick the folder (or pick the two files together). The dialog shows the
   pair it found and any problem with it.
4. Press **Upload**. The model appears in the Custom Models list.
5. Switch it on there, or tick it as the running model. Detection restarts
   on the new model; class names appear as you gave them.

The upload puts the files on the box's model disk as
`/mnt/nvme/model/<model>/<model>.hef` and `.json`. That is the same layout
you would make by hand, so a model copied over by `scp` into that folder
works identically.

## 5. Uploading by hand instead (optional)

If you have shell access:

```
scp -r my_model/ pi@<box-ip>:/mnt/nvme/model/
```

then press the reload arrow at the top right of the Custom Models card. The
two-file rule and the
naming rules above still apply.

## 6. If something is refused

| Message | Fix |
|---------|-----|
| the .hef and the .json must share one name | rename one so both are `<model>.hef` / `<model>.json` |
| "num_classes" says N but "classes" lists M | make them agree; `classes` is the authority |
| this model was compiled for X; this box has a Y | recompile for the box's chip, or upload to a box with that chip |
| a model called X is already in the folder | delete the old one from the Custom Models card first, or rename yours |
| the model folder is not writable | on the box: `sudo chown -R pi:pi /mnt/nvme/model` (or the user the dashboard runs as) |

## 7. Checklist before you upload

- [ ] one folder, two files, same name
- [ ] `.json` is valid JSON (paste it into a JSON checker if unsure)
- [ ] `classes` in the model's output order, `num_classes` matches
- [ ] `target` is the chip in the box
- [ ] no other files needed -- scripts, weights in other formats, READMEs stay on your PC
