# Build the static Linux executable from the pinned export with build_runtime.py
FROM scratch
COPY provisioner /provisioner
USER 65532:65532
ENTRYPOINT ["/provisioner"]
