"""Quick aiortc API introspection (development aid, no network)."""

from aiortc import RTCPeerConnection

print("EVENTS:", getattr(RTCPeerConnection, "EVENTS", None))

import inspect

import aiortc.rtcpeerconnection as m

src = inspect.getsource(m)
print("icecandidate refs:", [l.strip() for l in src.splitlines() if "icecandidate" in l][:8])
print("track refs:", [l.strip() for l in src.splitlines() if 'emit("track' in l][:3])
