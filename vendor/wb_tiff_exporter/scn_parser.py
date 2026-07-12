from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
import re
import struct
from typing import Callable
from xml.etree import ElementTree as ET

from tiff_writer import build_grayscale16_tiff


SUPPORTED_EXTENSIONS = {".scn", ".sscn", ".mscn", ".smscn"}
TIFF_CONTENT_TYPES = {"image/tiff", "image/x-tiff"}
TIFF_MAGICS = (b"II*\x00", b"MM\x00*")

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class TiffCandidate:
    source_label: str
    content_type: str
    filename: str | None
    payload_size: int
    tiff_header_detected: bool
    start_offset: int
    data: bytes
    extraction_note: str = ""


@dataclass(frozen=True)
class ScnInspection:
    source: Path
    mime_parse_succeeded: bool
    mime_parts_found: int
    candidates: list[TiffCandidate]


@dataclass(frozen=True)
class _PartPayload:
    index: int
    content_type: str
    filename: str | None
    description: str
    payload: bytes


@dataclass(frozen=True)
class _DisplayTransform:
    low: int
    high: int
    gamma: float = 1.0
    inverted: bool = False


def inspect_scn_file(
    source_path: Path,
    *,
    log: LogFn | None = None,
    debug: bool = False,
) -> ScnInspection:
    logger = log or (lambda _: None)
    source_path = source_path.expanduser().resolve()
    raw_bytes = source_path.read_bytes()

    logger(f"Parsing file: {source_path.name}")

    candidates: list[TiffCandidate] = []
    mime_parts_found = 0
    mime_parse_succeeded = False

    if _looks_like_mime(raw_bytes):
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
            leaf_parts = [part for part in message.walk() if not part.is_multipart()]
            raw_parts = _extract_content_length_parts(raw_bytes)
            raw_part_index = 0
            mime_parts_found = len(leaf_parts)
            mime_parse_succeeded = True
            logger("MIME parse succeeded.")
            logger(f"Found {mime_parts_found} MIME part(s).")

            parsed_parts: list[_PartPayload] = []
            for index, part in enumerate(leaf_parts, start=1):
                raw_part = None
                if part.get("Content-Length") is not None and raw_part_index < len(raw_parts):
                    raw_part = raw_parts[raw_part_index]
                    raw_part_index += 1
                payload = raw_part.payload if raw_part is not None else _get_payload_bytes(part)
                content_type = part.get_content_type()
                filename = part.get_filename()
                description = part.get("Content-Description", "")
                if raw_part is not None:
                    content_type = raw_part.content_type
                    filename = raw_part.filename
                    description = raw_part.description

                parsed_parts.append(
                    _PartPayload(
                        index=index,
                        content_type=content_type,
                        filename=filename,
                        description=description,
                        payload=payload,
                    )
                )

                offsets = find_tiff_offsets(payload)
                tiff_detected = bool(offsets)

                if debug:
                    logger(
                        f"Part {index:02d}: content-type={content_type}, "
                        f"filename={filename or '-'}, payload={len(payload)} byte(s), "
                        f"tiff_header_detected={'yes' if tiff_detected else 'no'}"
                    )

                if content_type in TIFF_CONTENT_TYPES:
                    if offsets:
                        candidates.extend(
                            _build_candidates_from_offsets(
                                payload,
                                offsets,
                                source_label=f"mime part {index:02d}",
                                content_type=content_type,
                                filename=filename,
                            )
                        )
                    elif payload:
                        candidates.append(
                            TiffCandidate(
                                source_label=f"mime part {index:02d}",
                                content_type=content_type,
                                filename=filename,
                                payload_size=len(payload),
                                tiff_header_detected=False,
                                start_offset=0,
                                data=payload,
                            )
                        )
                elif offsets:
                    candidates.extend(
                        _build_candidates_from_offsets(
                            payload,
                            offsets,
                            source_label=f"mime part {index:02d}",
                            content_type=content_type,
                            filename=filename,
                        )
                    )
                elif content_type == "application/octet-stream":
                    raw_candidate = _extract_biorad_raw_image_candidate(
                        payload,
                        source_label=f"mime part {index:02d}",
                        content_type=content_type,
                        filename=filename,
                        log=logger,
                        debug=debug,
                    )
                    if raw_candidate is not None:
                        candidates.append(raw_candidate)

            candidates.extend(
                _extract_biorad_raw_image_candidates_from_parts(
                    parsed_parts,
                    existing_source_labels={candidate.source_label for candidate in candidates},
                    log=logger,
                    debug=debug,
                )
            )
        except Exception as exc:
            logger(f"MIME parse failed: {exc}")
    else:
        logger("MIME parse failed: file does not look like a MIME container.")

    if not candidates:
        logger("No TIFF payload found during MIME inspection. Trying raw TIFF scan.")
        raw_offsets = find_tiff_offsets(raw_bytes)
        if debug:
            logger(f"Raw scan found {len(raw_offsets)} TIFF header candidate(s).")
        candidates.extend(
            _build_candidates_from_offsets(
                raw_bytes,
                raw_offsets,
                source_label="raw file scan",
                content_type="application/octet-stream",
                filename=None,
            )
        )

    return ScnInspection(
        source=source_path,
        mime_parse_succeeded=mime_parse_succeeded,
        mime_parts_found=mime_parts_found,
        candidates=candidates,
    )


def print_mime_structure(source_path: Path, out: LogFn = print) -> None:
    source_path = source_path.expanduser().resolve()
    raw_bytes = source_path.read_bytes()

    out(f"Inspecting MIME structure for {source_path}")
    out(f"File size: {len(raw_bytes)} byte(s)")

    if not _looks_like_mime(raw_bytes):
        out("This file does not look like a MIME container from its leading bytes.")
        return

    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    except Exception as exc:
        out(f"MIME parse failed: {exc}")
        return

    out(f"Top-level content-type: {message.get_content_type()}")
    leaf_index = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        leaf_index += 1
        payload = _get_payload_bytes(part)
        offsets = find_tiff_offsets(payload)
        out(
            f"Part {leaf_index:02d}: content-type={part.get_content_type()}, "
            f"filename={part.get_filename() or '-'}, payload={len(payload)} byte(s), "
            f"tiff_header_detected={'yes' if bool(offsets) else 'no'}"
        )


def find_tiff_offsets(data: bytes) -> list[int]:
    offsets: set[int] = set()
    for magic in TIFF_MAGICS:
        search_start = 0
        while True:
            index = data.find(magic, search_start)
            if index == -1:
                break
            offsets.add(index)
            search_start = index + 1
    return sorted(offsets)


def _looks_like_mime(raw_bytes: bytes) -> bool:
    head = raw_bytes[:4096]
    mime_markers = (b"Content-Type:", b"MIME-Version:", b"boundary=", b"Content-Transfer-Encoding:")
    return any(marker in head for marker in mime_markers)


def _get_payload_bytes(part: EmailMessage) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload

    raw_payload = part.get_payload()
    if isinstance(raw_payload, list):
        return b""
    if isinstance(raw_payload, str):
        return raw_payload.encode("utf-8", errors="ignore")
    return b""


def _extract_content_length_parts(raw_bytes: bytes) -> list[_PartPayload]:
    """Extract Image Lab MIME payloads by Content-Length without line-ending loss."""

    parts: list[_PartPayload] = []
    cursor = 0
    index = 0
    while True:
        length_match = re.search(br"(?im)^Content-Length:\s*(\d+)\s*$", raw_bytes[cursor:])
        if length_match is None:
            break

        header_start = raw_bytes.rfind(b"\r\n--", 0, cursor + length_match.start())
        if header_start == -1:
            cursor += length_match.end()
            continue
        header_start += 2

        header_end = raw_bytes.find(b"\r\n\r\n", cursor + length_match.end())
        if header_end == -1:
            break

        payload_start = header_end + 4
        payload_length = int(length_match.group(1))
        payload_end = payload_start + payload_length
        if payload_end > len(raw_bytes):
            break

        header_block = raw_bytes[header_start:header_end].decode("utf-8", errors="replace")
        header_map: dict[str, str] = {}
        for line in header_block.split("\r\n"):
            key, separator, value = line.partition(":")
            if separator:
                header_map[key.lower()] = value.strip()

        index += 1
        parts.append(
            _PartPayload(
                index=index,
                content_type=header_map.get("content-type", "application/octet-stream").split(";", 1)[0],
                filename=_filename_from_content_disposition(header_map.get("content-disposition", "")),
                description=header_map.get("content-description", ""),
                payload=raw_bytes[payload_start:payload_end],
            )
        )
        cursor = payload_end

    return parts


def _filename_from_content_disposition(value: str) -> str | None:
    if not value:
        return None
    for segment in value.split(";"):
        key, separator, raw_value = segment.strip().partition("=")
        if separator and key.lower() == "filename":
            filename = raw_value.strip().strip('"')
            return filename or None
    return None


def _extract_biorad_raw_image_candidates_from_parts(
    parts: list[_PartPayload],
    *,
    existing_source_labels: set[str],
    log: LogFn,
    debug: bool,
) -> list[TiffCandidate]:
    candidates: list[TiffCandidate] = []
    metadata_parts = [part for part in parts if part.content_type == "text/xml"]
    display_transform = _extract_display_transform_from_parts(metadata_parts)
    image_parts = [
        part
        for part in parts
        if part.content_type == "application/octet-stream"
        and part.description.lower() in {"", "imagedata"}
    ]

    for image_part in image_parts:
        source_label = f"mime part {image_part.index:02d}"
        if source_label in existing_source_labels:
            continue

        for metadata_part in metadata_parts:
            candidate = _extract_biorad_raw_image_candidate_from_metadata(
                image_part.payload,
                metadata_part.payload,
                source_label=(
                    f"mime part {image_part.index:02d} + metadata part "
                    f"{metadata_part.index:02d}"
                ),
                content_type=image_part.content_type,
                filename=image_part.filename,
                display_transform=display_transform,
                log=log,
                debug=debug,
            )
            if candidate is not None:
                candidates.append(candidate)
                break

    return candidates


def _extract_biorad_raw_image_candidate_from_metadata(
    image_payload: bytes,
    metadata_payload: bytes,
    *,
    source_label: str,
    content_type: str,
    filename: str | None,
    display_transform: _DisplayTransform | None,
    log: LogFn,
    debug: bool,
) -> TiffCandidate | None:
    xml_root_offset = metadata_payload.find(b"<root")
    xml_end_offset = metadata_payload.find(b"</root>", xml_root_offset)
    if xml_root_offset == -1 or xml_end_offset == -1:
        return None

    xml_bytes = metadata_payload[xml_root_offset : xml_end_offset + len(b"</root>")]
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="strict"))
    except Exception:
        return None

    size_pix = root.find("size_pix")
    if size_pix is None:
        return None

    try:
        width = int(size_pix.attrib["width"])
        height = int(size_pix.attrib["height"])
    except (KeyError, ValueError):
        return None

    expected_raw_bytes = width * height * 2
    if expected_raw_bytes <= 0 or expected_raw_bytes > len(image_payload):
        return None

    image_element = root.find("image")
    white_is_zero = True
    if image_element is not None:
        white_is_zero = image_element.attrib.get("zero_is", "white").lower() == "white"

    endian = (root.findtext("endian") or "little").strip().lower()
    if endian not in {"little", "big"}:
        endian = "little"

    raw_pixels = image_payload[:expected_raw_bytes]
    if display_transform is not None:
        raw_pixels = _apply_display_transform_to_raw_pixels(
            raw_pixels,
            byteorder=endian,
            transform=display_transform,
        )

    if debug:
        transform_note = ""
        if display_transform is not None:
            transform_note = (
                f", display_low={display_transform.low}, "
                f"display_high={display_transform.high}, gamma={display_transform.gamma}, "
                f"display_inverted={display_transform.inverted}"
            )
        log(
            f"{source_label}: detected Bio-Rad raw image payload "
            f"({width}x{height}, endian={endian}, raw_bytes={expected_raw_bytes}"
            f"{transform_note})"
        )

    extraction_note = f"wrapped raw {width}x{height} 16-bit payload into TIFF"
    if display_transform is not None:
        extraction_note += (
            f" with Image Lab display stretch {display_transform.low}-"
            f"{display_transform.high}"
        )

    return TiffCandidate(
        source_label=source_label,
        content_type=content_type,
        filename=filename,
        payload_size=len(raw_pixels),
        tiff_header_detected=False,
        start_offset=0,
        data=build_grayscale16_tiff(
            raw_pixels,
            width=width,
            height=height,
            byteorder=endian,
            white_is_zero=white_is_zero,
        ),
        extraction_note=extraction_note,
    )


def _extract_display_transform_from_parts(parts: list[_PartPayload]) -> _DisplayTransform | None:
    for part in parts:
        transform = _extract_display_transform_from_xml(part.payload)
        if transform is not None:
            return transform
    return None


def _extract_display_transform_from_xml(xml_payload: bytes) -> _DisplayTransform | None:
    xml_root_offset = xml_payload.find(b"<root")
    xml_end_offset = xml_payload.find(b"</root>", xml_root_offset)
    if xml_root_offset == -1 or xml_end_offset == -1:
        return None

    xml_bytes = xml_payload[xml_root_offset : xml_end_offset + len(b"</root>")]
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="strict"))
    except Exception:
        return None

    transform_element = root.find(".//transform")
    if transform_element is None:
        return None

    try:
        low_frac = float(transform_element.attrib["low_frac"])
        high_frac = float(transform_element.attrib["high_frac"])
    except (KeyError, ValueError):
        return None

    max_value = 65535
    scanner_element = root.find(".//scanner")
    if scanner_element is not None:
        try:
            max_value = int(float(scanner_element.attrib.get("max_value", max_value)))
        except ValueError:
            max_value = 65535

    low = int(round(max(0.0, min(1.0, low_frac)) * max_value))
    high = int(round(max(0.0, min(1.0, high_frac)) * max_value))
    if high <= low:
        return None

    try:
        gamma = float(transform_element.attrib.get("gamma", "1"))
    except ValueError:
        gamma = 1.0
    gamma = max(0.1, min(4.0, gamma))
    inverted = transform_element.attrib.get("invert", "false").strip().lower() == "true"

    return _DisplayTransform(low=low, high=high, gamma=gamma, inverted=inverted)


def _apply_display_transform_to_raw_pixels(
    pixel_bytes: bytes,
    *,
    byteorder: str,
    transform: _DisplayTransform,
) -> bytes:
    if byteorder not in {"little", "big"}:
        byteorder = "little"
    if len(pixel_bytes) % 2 != 0:
        return pixel_bytes

    pack_prefix = "<" if byteorder == "little" else ">"
    count = len(pixel_bytes) // 2
    values = struct.unpack(f"{pack_prefix}{count}H", pixel_bytes)
    span = max(1, transform.high - transform.low)
    out = bytearray(len(pixel_bytes))
    for index, value in enumerate(values):
        normalized = (min(max(value, transform.low), transform.high) - transform.low) / span
        if abs(transform.gamma - 1.0) > 1e-6:
            normalized = normalized ** transform.gamma
        if transform.inverted:
            normalized = 1.0 - normalized
        scaled = int(round(normalized * 65535.0))
        struct.pack_into(f"{pack_prefix}H", out, index * 2, max(0, min(65535, scaled)))
    return bytes(out)


def _build_candidates_from_offsets(
    payload: bytes,
    offsets: list[int],
    *,
    source_label: str,
    content_type: str,
    filename: str | None,
) -> list[TiffCandidate]:
    candidates: list[TiffCandidate] = []
    for index, start_offset in enumerate(offsets):
        end_offset = offsets[index + 1] if index + 1 < len(offsets) else len(payload)
        chunk = payload[start_offset:end_offset]
        if len(chunk) < 8:
            continue
        candidates.append(
            TiffCandidate(
                source_label=source_label,
                content_type=content_type,
                filename=filename,
                payload_size=len(chunk),
                tiff_header_detected=True,
                start_offset=start_offset,
                data=chunk,
                extraction_note="direct TIFF header detected",
            )
        )
    return candidates


def _extract_biorad_raw_image_candidate(
    payload: bytes,
    *,
    source_label: str,
    content_type: str,
    filename: str | None,
    log: LogFn,
    debug: bool,
) -> TiffCandidate | None:
    xml_doctype_offset = payload.find(b"<!DOCTYPE XML>")
    if xml_doctype_offset == -1:
        return None

    xml_root_offset = payload.find(b"<root>", xml_doctype_offset)
    xml_end_offset = payload.find(b"</root>", xml_root_offset)
    if xml_root_offset == -1 or xml_end_offset == -1:
        return None

    xml_bytes = payload[xml_root_offset : xml_end_offset + len(b"</root>")]
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="strict"))
    except Exception:
        return None

    size_pix = root.find("size_pix")
    if size_pix is None:
        return None

    try:
        width = int(size_pix.attrib["width"])
        height = int(size_pix.attrib["height"])
    except (KeyError, ValueError):
        return None

    expected_raw_bytes = width * height * 2
    if expected_raw_bytes <= 0 or expected_raw_bytes > len(payload):
        return None

    raw_pixels = payload[:expected_raw_bytes]
    image_element = root.find("image")
    white_is_zero = True
    if image_element is not None:
        white_is_zero = image_element.attrib.get("zero_is", "white").lower() == "white"

    endian = (root.findtext("endian") or "little").strip().lower()
    if endian not in {"little", "big"}:
        endian = "little"

    if debug:
        log(
            f"{source_label}: detected Bio-Rad raw image payload "
            f"({width}x{height}, endian={endian}, raw_bytes={expected_raw_bytes})"
        )

    return TiffCandidate(
        source_label=source_label,
        content_type=content_type,
        filename=filename,
        payload_size=len(raw_pixels),
        tiff_header_detected=False,
        start_offset=0,
        data=build_grayscale16_tiff(
            raw_pixels,
            width=width,
            height=height,
            byteorder=endian,
            white_is_zero=white_is_zero,
        ),
        extraction_note=f"wrapped raw {width}x{height} 16-bit payload into TIFF",
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 scn_parser.py /path/to/file.scn")
        raise SystemExit(1)

    print_mime_structure(Path(sys.argv[1]))
