FROM golang:1.24 AS build
RUN git clone -q --depth 1 --branch @SS_SERVER_VERSION@ https://github.com/Jigsaw-Code/outline-ss-server /src
WORKDIR /src
RUN sed -i 's/^\(func RequirePublicIP(ip net.IP) error {\)$/\1\n\treturn nil/' net/private_net.go \
 && grep -A1 'func RequirePublicIP' net/private_net.go | grep -q 'return nil' \
 && CGO_ENABLED=0 go build -o /outline-ss-server ./cmd/outline-ss-server
FROM @BASE_IMAGE@
COPY --from=build /outline-ss-server /opt/outline-server/bin/outline-ss-server
