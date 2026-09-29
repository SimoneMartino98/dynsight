The Label Tool
==============

The ``dynsight label_tool`` is a local web application for labeling images
and building training datasets. Picture labelling is a crucial step in many
computer vision tasks, such as the creation of the initial dataset used to
train Convolutional Neural Networks (CNNs). The current version of
`dynsight vision <../_autosummary/dynsight.vision.VisionInstance.html>`_
exploits the power of the `YOLO models <https://docs.ultralytics.com/models/yolo12/>`_
for computer vision tasks. The ``label_tool`` writes datasets directly
in the YOLO format expected by
`set_training_dataset <../_autosummary/dynsight.vision.VisionInstance.html#dynsight.vision.VisionInstance.set_training_dataset>`_,
so they can be used for training without any manual editing.

.. image:: ../_static/label_tool.png

----------
How to Use
----------

The ``label_tool`` application can be executed in 2 main ways:

* As a standalone application, run the following command in the environment
  where dynsight is installed:

.. code-block:: bash

    $ label_tool

* From python code:

.. code-block:: python

    import dynsight

    dynsight.vision.label_tool(
        port=8888,  # optional
        workspace="my_workspace",  # optional
    )

In both cases a localhost server should start and the application should
automatically appear in your default web browser.

.. tip::

    In case the application does
    not open automatically, you can manually open it by copying and pasting
    the URL provided in the terminal output.

All uploaded images are stored inside the *workspace* directory
(``./label_tool_workspace`` by default). The active session holds labels,
boxes, region reviews, queue decisions, and comparison links together.
Use *Save session* to write its JSON file. Frame copies are saved in a
sibling ``<session stem>_frames`` directory, and comparison reports in
``<session stem>_comparisons``. Keep these directories with the JSON when
moving a session. On restart, the tool reopens the last saved session in
that workspace when its files are available. Unsaved edits remain only in
server memory; *Quit* asks whether to save them first.

-------
The GUI
-------

The Graphical User Interface is divided in three main panels:

* **The Labels panel** (top left): create the object classes. Each label
  shows its YOLO class ID, its color and the number of boxes drawn with it.
  Class IDs follow the order of this list and are stable across exports.

* **The Images panel** (bottom left): add content with ``+ Images`` or
  ``+ Video`` (frames are extracted at a chosen interval), or by dragging
  and dropping files onto the canvas. Each entry shows a thumbnail, review
  status, test-set status, and annotation count. Select a frame and use
  **Reviewed** after checking all boxes. A reviewed frame with no boxes is a
  valid negative example. **Test set** is available only for reviewed frames.

  **Server images…** and **Server video…** browse files on the machine running
  the label tool. Select one or more images and choose **Import selected**;
  select a video, set the frame interval, and extract. These sources are read
  directly from that machine, so a large remote video need not be uploaded
  through the browser. The ``+`` buttons and drag-and-drop continue to upload
  files from the computer running the web browser.

* **The Canvas** (right): displays the current image and the bounding
  boxes.

Annotations are done directly on the canvas:

* **Draw**: select a label, then click and drag.
* **Select**: click a box.
* **Move / resize**: drag a selected box, or drag one of its handles.
* **Change label**: select a box, then click a different label.
* **Delete**: right-click a box, or select it and press backspace.
* **Navigate**: mouse wheel to zoom, space (or middle mouse) drag to pan,
  arrow keys to switch image.

Two export options are available in the top bar. Both write the dataset
folder directly to disk (inside the workspace by default) together with a
ready-to-use ``dataset.yaml``:

* **Export dataset**: exports only reviewed frames outside the test set as a
  YOLO dataset, with a configurable train/validation split. Frames from the
  same source video stay in one split when multiple sources exist. With just
  one source, the exported ``frame_manifest.json`` explicitly identifies the
  correlated frame split. Older sessions load with frames unreviewed until
  they are explicitly checked. Sources represented in the frozen test set
  are excluded from training export.

* **Synthesize**: creates a synthetic dataset from reviewed, non-test crops
  pasted at random, non-overlapping positions onto a uniform background or
  onto **verified empty real regions**. The ``source_manifest.json`` records
  crop and background sources. With multiple sources, train and validation
  are grouped by source. A single-source split remains correlated and is
  identified in the manifest.

-------------------------------
Review regions and draft support
-------------------------------

The **Region review** panel divides the selected frame into 256-pixel tiles.
Select a tile to zoom into it. Mark it verified only after checking every
object inside, including border-cut boxes; an empty verified tile is a
valid negative. Edits to any boxes on the frame clear its tile verification
so it can be checked again. **Export verified regions** writes only those
tiles, including clipped boxes that intersect their borders, and records
their source pixels in ``region_manifest.json``. A partly reviewed frame
does not become reviewed ground truth. Frozen test sources are excluded.

**Rank tiles** proposes a review queue using confidence, uncertain boxes,
border crossings, unusual shape, count changes, and any saved comparison's
misses or false positives. It also includes ordinary and empty examples.
Accept, reject, and defer decisions remain in the same session. The queue
does not change annotations on its own.

After a whole keyframe is reviewed, **Suggest boxes on next frame** uses
optical flow and existing drafts to propose boxes on the next unreviewed
frame from the same video. It preserves unmatched existing drafts. All
propagated boxes remain unreviewed, and uncertain moves are flagged.

**Export cautious pseudo-labels** is a separate, explicitly model-generated
dataset. Only high-confidence draft boxes with a same-class spatial match
in a nearby frame are admitted. The policy and provenance are written to
``pseudo_manifest.json``; frozen test sources are excluded. Full-frame
images can still contain unlabelled objects, so this export must not be
treated as reviewed ground truth.

---------------------------
Review and compare models
---------------------------

Use **Import predictions** to select a YOLO checkpoint. The tool runs it on
unreviewed, non-test images and loads its boxes as editable drafts, retaining
their confidence and model path in the session. Correct the boxes and mark
each checked frame **Reviewed**. Existing reviewed and test frames are not
overwritten by another prediction import. Set confidence, NMS IoU, image
size, maximum detections, and device to match the run being reviewed.

The **Browse…** buttons beside session, checkpoint, and benchmark paths open
a file browser on the machine running the label tool. Use **Up**, enter a
directory path with **Go**, or filter names in the current folder. Select a
file to fill its path. In **Compare models**, **Add…** appends checkpoints one
at a time. For a new session, browse to a directory and choose **Use this
folder** to fill a ``session.json`` path. The picker lists paths only; it does
not load a session or run a model until the corresponding dialog is submitted.

Mark a selection of reviewed real frames **Test set**, then choose **Freeze
benchmark**. This writes a JSON snapshot under ``workspace/benchmarks`` with
the reviewed boxes, source frame numbers where available, and image hashes.
Keep its path for future comparisons. The snapshot remains fixed if the
working session is edited later, and its source images cannot be replaced or
deleted from the label tool.

Use **Compare models** with that benchmark path and one checkpoint path per
line. The tool uses the same images and inference settings for every model.
It opens a comparison view *inside the active label-tool session* with
precision-recall and F1 curves, count error, sortable frame rows, and
synchronized side-by-side overlays. Green marks matched reviewed truth,
yellow missed truth, cyan matched predictions, and red false positives.
The view explains metric denominators and can be reopened from **Session
comparisons** after restart. It supports browser printing to PDF.

Each comparison saves ``report.json``, standalone ``index.html``, selected
overlay screenshots, and SVG curves under ``workspace/comparisons/<id>``.
The session keeps the report link and bundles these files when saved. The
report records image and checkpoint hashes, class mappings, effective
settings, matching rules, per-frame matches, and source-overlap warnings.
Opening a saved comparison warns if an input changed. Matching is
same-class and greedy by prediction confidence at the chosen IoU; curves
only include thresholds above the inference confidence floor. The metrics
describe the selected reviewed frames, not accuracy on the whole video.
