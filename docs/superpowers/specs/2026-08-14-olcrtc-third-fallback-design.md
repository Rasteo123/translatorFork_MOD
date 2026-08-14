# olcRTC as a Third VPN Fallback

Date: 2026-08-14

## Objective

Add olcRTC to the existing Routerich/VPS network as a third, lazily activated
fallback. The routing priority must remain:

1. AmneziaWG (`awg0`)
2. NaiveProxy (`tun-naive`)
3. olcRTC over Jitsi DataChannel (`tun-olcrtc`)
4. Direct WAN as the final fail-open path

The olcRTC client and its TUN adapter must run only while both higher-priority
tunnels are confirmed unavailable. Recovery of AmneziaWG or NaiveProxy must
move traffic back to the recovered higher-priority tunnel and stop the olcRTC
client-side services.

## Current Environment

The router is a Routerich AX3000 v1 running RouteRich 24.10.5 on ARM64. It has
sing-box 1.13.12, PBR, an AmneziaWG interface named `awg0`, and a NaiveProxy
TUN interface named `tun-naive`. The existing failover daemon changes only the
two split-default routes in the main table and leaves the RU-domain WAN policy
untouched.

The olcRTC server will run on the Ubuntu 24.04 x86_64 VPS at `91.107.201.91`.
The existing AmneziaWG and NaiveProxy services use a different server and are
outside this installation's server-side scope.

## Source and Transport Choice

Use the official `openlibrecommunity/olcrtc` repository, pinned to reviewed
commit `48cae636f88e16863c99d4147bbc327a856cdf00`. Build static Linux AMD64 and
ARM64 binaries with `CGO_ENABLED=0`, record their SHA-256 hashes, and install
only those verified artifacts. Do not execute remote `curl | bash` installers.

Use Jitsi plus DataChannel. This is the project's recommended combination and
avoids the removed Telemost DataChannel. Configure matching olcRTC profiles on
both hosts: `conference.ct.placetime.team` first and `meet.mamba.group` second,
with a separate high-entropy room name for each profile. Both instances were
reachable from the router's physical WAN and from the VPS during design. Set
profile retry delay to five seconds with unlimited cycles. The 32-byte olcRTC
encryption key is generated locally, stored in root-readable key files on both
hosts, and never written to logs or the design document.

## Components

### VPS

- `/usr/local/bin/olcrtc`: pinned static AMD64 binary.
- `/etc/olcrtc/server.yaml`: server-mode Jitsi/DataChannel configuration.
- `/etc/olcrtc/olcrtc.key`: shared encryption key, mode `0600`.
- `olcrtc.service`: an enabled systemd service running continuously as an
  unprivileged dedicated user with restart-on-failure and filesystem/process
  hardening.

The VPS service waits in the private Jitsi room and provides outbound TCP
connections for the router-side client. It does not open a public proxy port.

### Router

- `/usr/bin/olcrtc`: pinned static ARM64 binary.
- `/etc/olcrtc/client.yaml`: client-mode configuration listening on
  `127.0.0.1:8808` only.
- `/etc/olcrtc/olcrtc.key`: shared encryption key, mode `0600`.
- `olcrtc-client`: a procd service that is disabled for normal boot and started
  only by the failover controller.
- `/etc/sing-box/olcrtc-tun.json`: a second sing-box instance with a TUN inbound
  named `tun-olcrtc` and a SOCKS outbound to `127.0.0.1:8808`.
- `sing-box-olcrtc`: a procd service disabled for normal boot and controlled
  together with `olcrtc-client`.

olcRTC is TCP-only. Existing router DNS continues through the current
dnsmasq -> dns-failsafe-proxy -> DoH/Stubby chain. UDP/443 through the olcRTC
fallback is rejected so QUIC clients retry over TCP promptly instead of
waiting for a UDP timeout.

## Underlay Routing and Loop Prevention

The WebRTC connection is the underlay and must never be routed back through
`tun-olcrtc`. Run the olcRTC client under a dedicated router user. Mark locally
generated packets from that UID in the existing PBR output chain and route the
mark through the WAN table. This applies to DNS, HTTPS/WebSocket, ICE, STUN,
and media packets without depending on changing Jitsi IP addresses.

Before installation, validate on this firmware that an nftables output rule
can match the dedicated numeric UID and that the resulting PBR mark selects the
WAN table. If that preflight fails, stop without installing the client; static
Jitsi IP exceptions are not an acceptable substitute because those addresses
can change.

The sing-box TUN adapter runs separately and is not given the WAN-only mark.
LAN TCP traffic entering `tun-olcrtc` therefore reaches the local SOCKS port,
while the olcRTC process's own WebRTC packets leave through physical WAN.

## Failover State Machine

The controller preserves the current split-default routing model and adds an
`olcrtc` state:

```text
AWG healthy                         -> route via awg0; stop olcRTC client stack
AWG down, Naive healthy             -> route via tun-naive; stop olcRTC client stack
AWG down, Naive down                -> start olcRTC client stack and probe it
olcRTC healthy                      -> route via tun-olcrtc
all three unavailable               -> remove VPN split routes and use WAN
AWG or Naive recovers               -> switch first, then stop olcRTC client stack
```

Each running tunnel is checked every ten seconds. A cycle first tries an HTTPS
connection to `1.1.1.1`; only if that transport attempt fails does it try
`8.8.8.8`. Any completed TLS/HTTP exchange counts as connectivity regardless
of HTTP status. Three failed cycles mark a tunnel dead; two successful cycles
mark it recovered. After a route change, a 30-second hold-down suppresses any
lower-priority route change, while recovery to a higher-priority tunnel remains
allowed. The off-state olcRTC tunnel is not probed until both higher-priority
tunnels are dead.

Only one long-running controller owns probe host routes and split-default
routes. Interface hotplug sends the controller a signal for an early check
instead of spawning a second route-changing process. This removes the current
possibility of overlapping one-shot probes. UCI, the RU-domain PBR table,
dnsmasq, and permanent interface configuration are not rewritten at runtime.

For olcRTC activation, the controller starts `olcrtc-client`, waits for the
SOCKS listener, starts `sing-box-olcrtc`, waits for `tun-olcrtc`, and requires
successful end-to-end probes before applying split-default routes. A startup
timeout of 60 seconds stops both client services and returns to direct WAN
instead of leaving a half-created route.

## NaiveProxy Observation

NaiveProxy is currently healthy: 15 consecutive direct probes through
`tun-naive` succeeded at approximately 140 ms. Earlier logs show a real,
temporary HTTP/2 underlay failure (`http2 ping failed`) followed by recovery.
The existing two-failure/two-success thresholds made that transient failure
appear as repeated `dead`/`alive` flapping. IPv6 and UDP warnings are expected
for the current TCP-only Naive outbound and were not the cause of the HTTP
health-check failure.

The new controller retains NaiveProxy as priority two but gives its health
decision better hysteresis and multiple TCP targets. No Naive credentials or
transport parameters are changed as part of this work.

## Installation Sequence

1. Capture current service status, routes, PBR rules, and checksums; make
   timestamped backups of every router and VPS file that may be replaced.
2. Build pinned AMD64 and ARM64 olcRTC binaries in an isolated temporary build
   environment and verify architecture, static linkage, and hashes. Continue
   only if the ARM64 binary fits the router overlay while leaving at least
   10 MiB free after all new files are installed.
3. Install and start the hardened VPS service. Confirm that it reaches the
   chosen Jitsi room without opening a public listening port.
4. Install the router binary, configuration, UID routing rule, and two disabled
   client services. Validate both configurations before starting them.
5. Start the router client stack manually without changing default routes.
   Verify the local SOCKS connection and a narrow test route through
   `tun-olcrtc`, then stop the stack.
6. Replace the failover controller atomically, restart it, and confirm that the
   normal state remains AmneziaWG with both olcRTC client services stopped.
7. Exercise the state-machine logic with stubbed health results, then perform a
   controlled end-to-end fallback test that does not disable LAN access or SSH.

## Verification

The installation is accepted only when all of the following hold:

- The installed binary hashes match the locally built artifacts.
- olcRTC server and client configurations parse successfully.
- VPS service restarts cleanly and exposes no unintended public port.
- Router SOCKS and `tun-olcrtc` tests reach the internet through the VPS.
- With AWG healthy, routing remains on `awg0` and the olcRTC client stack is
  stopped.
- Simulated AWG failure selects NaiveProxy; simulated failure of both selects
  olcRTC; failure of all three selects direct WAN.
- Recovery selects the highest healthy priority and stops olcRTC when it is no
  longer needed.
- RU-domain WAN policy and router DNS remain operational in every state.
- No rapid route flapping occurs during transient single-probe failures.

## Rollback

A timestamped rollback script restores the original failover controller,
configuration, init scripts, PBR include, and routes. If any installation or
verification step fails, stop and disable the two router olcRTC services,
restore the backups, reload PBR, restore the AWG split-default routes when AWG
is healthy, and verify the pre-change state. Server-side olcRTC files can then
be disabled and removed independently because the new server has no dependency
from either existing tunnel.
