# License inventory

`third-party/` contains byte-for-byte license files fetched from the exact upstream
commits pinned in `experiments/manifests/third-party-skills.json`. Filenames are the
GitHub `owner--repository` pair. `skillrace.third_party_audit` requires one embedded
copy for every distributed headline source and rejects local content for unsafe
license exclusions.
