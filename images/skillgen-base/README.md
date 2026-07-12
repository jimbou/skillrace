# Skill-generation base image

This directory is the source of `skillrace/skillgen-base`, the shared Python,
pytest, Git, and Pi environment used by the RQ1 skill images and RQ3 scenarios.

`base-image.lock.json` binds the build to the reviewed `pi-base` image ID. The
Dockerfile also pins the Debian Python and pytest package versions. Run:

```bash
images/skillgen-base/build.sh
```

The script refuses to build if the local `skillrace/pi-base:0.62.0` tag has moved.
Changing that parent or any package version is a protocol change and requires a new
lock, image date tag, and downstream image/oracle refresh.
