# Skill-generation base image

This directory is the source of `skillrace/skillgen-base`, the shared Python,
pytest, Git, and Pi environment used by the RQ1 skill images and RQ3 scenarios.

`base-image.lock.json` binds composition to the reviewed Pi 0.73.1 image and the
source-built Python 3.11.2/pytest 7.2.1 bootstrap. The slow bootstrap has its own
`Dockerfile.python-bootstrap`; normal provider/model changes reuse its immutable layer.
Run:

```bash
images/skillgen-base/build.sh
```

The script refuses to build if either input image ID has moved. It emits
`skillrace/skillgen-base:0.73.1-construction`. D1 then builds each heavy skill
environment once and adds a tiny, audited model-catalog overlay for each complete
experiment track. Changing Pi, Python, pytest, either input identity, or either catalog
is a protocol change and requires downstream image/oracle refresh.
