"""Direct UPnP AVTransport push to the amps' per-zone DLNA renderers.

Each Axium amp exposes one MediaRenderer per physical channel but advertises
only ONE of them over SSDP, so Home Assistant / Music Assistant can't
auto-discover them all. Notifications don't need discovery: given a zone's
AVTransport control URL (``http://<amp-ip>/upnp/av_transport_ctrl<index>``,
index = physical channel - 1) we push the sound straight to it with
``SetAVTransportURI`` + ``Play``.

Verified on an AX-800-X (fw 5.6.0): the amp fetches the media URL and plays it.
Loudness stays governed by the control protocol (0x04), not the renderer's
RenderingControl volume (which the amp stores but does not apply to output), so
the notification volume is set on the Axium zone, not here.
"""

from __future__ import annotations

import logging
import re
from xml.sax.saxutils import escape

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

AVT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_STATE_RE = re.compile(r"<CurrentTransportState>(.*?)</CurrentTransportState>")

#: Transport states that mean the renderer is still busy with the sound.
ACTIVE_STATES = frozenset({"PLAYING", "TRANSITIONING"})


def _envelope(body: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


async def _call(
    hass: HomeAssistant, control_url: str, action: str, body: str
) -> str:
    """POST one SOAP action to an AVTransport control URL; return the response."""
    session = async_get_clientsession(hass)
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{AVT_SERVICE}#{action}"',
    }
    async with session.post(
        control_url, data=_envelope(body), headers=headers, timeout=_TIMEOUT
    ) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"{action} -> HTTP {resp.status}: {text[:160]}")
        return text


def _didl(url: str, title: str, mime: str) -> str:
    """Minimal DIDL-Lite metadata for a single audio item."""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        f'<res protocolInfo="http-get:*:{escape(mime)}:*">{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


async def async_push(
    hass: HomeAssistant,
    control_url: str,
    media_url: str,
    title: str = "Notification",
    mime: str = "audio/mpeg",
) -> None:
    """Load and play ``media_url`` on a renderer's AVTransport control URL."""
    meta = escape(_didl(media_url, title, mime))
    await _call(
        hass,
        control_url,
        "SetAVTransportURI",
        f'<u:SetAVTransportURI xmlns:u="{AVT_SERVICE}">'
        f"<InstanceID>0</InstanceID><CurrentURI>{escape(media_url)}</CurrentURI>"
        f"<CurrentURIMetaData>{meta}</CurrentURIMetaData></u:SetAVTransportURI>",
    )
    await _call(
        hass,
        control_url,
        "Play",
        f'<u:Play xmlns:u="{AVT_SERVICE}">'
        "<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>",
    )


async def async_transport_state(
    hass: HomeAssistant, control_url: str
) -> str | None:
    """Return the renderer's CurrentTransportState, or None on error."""
    try:
        text = await _call(
            hass,
            control_url,
            "GetTransportInfo",
            f'<u:GetTransportInfo xmlns:u="{AVT_SERVICE}">'
            "<InstanceID>0</InstanceID></u:GetTransportInfo>",
        )
    except (aiohttp.ClientError, RuntimeError, TimeoutError) as err:
        _LOGGER.debug("GetTransportInfo %s failed: %s", control_url, err)
        return None
    match = _STATE_RE.search(text)
    return match.group(1) if match else None


async def async_stop(hass: HomeAssistant, control_url: str) -> None:
    """Best-effort Stop, to silence a renderer when a notification ends early."""
    try:
        await _call(
            hass,
            control_url,
            "Stop",
            f'<u:Stop xmlns:u="{AVT_SERVICE}">'
            "<InstanceID>0</InstanceID></u:Stop>",
        )
    except (aiohttp.ClientError, RuntimeError, TimeoutError) as err:
        _LOGGER.debug("Stop %s failed: %s", control_url, err)
