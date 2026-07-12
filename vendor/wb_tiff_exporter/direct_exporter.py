from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from scn_parser import TiffCandidate, inspect_scn_file


LogFn = Callable[[str], None]


class DirectExportError(RuntimeError):
    """Raised when the direct SCN export workflow cannot continue."""


@dataclass(frozen=True)
class FileExportResult:
    source: Path
    exported_files: list[Path]
    mime_parse_succeeded: bool
    mime_parts_found: int
    candidate_count: int


class DirectExporter:
    def export_documents(
        self,
        document_paths: Iterable[Path],
        output_dir: Path,
        *,
        debug: bool = False,
        log: LogFn | None = None,
    ) -> list[FileExportResult]:
        logger = log or (lambda _: None)
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        results: list[FileExportResult] = []

        for raw_path in document_paths:
            source_path = raw_path.expanduser().resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"Source file does not exist: {source_path}")

            inspection = inspect_scn_file(source_path, log=logger, debug=debug)
            logger(f"Found {len(inspection.candidates)} candidate part(s).")

            exported_files: list[Path] = []
            if inspection.candidates:
                exported_files = self._write_candidates(
                    source_path,
                    inspection.candidates,
                    output_dir,
                    logger,
                )
            else:
                logger("No TIFF payload found.")
                logger(f"Direct parsing failed for {source_path.name}.")

            results.append(
                FileExportResult(
                    source=source_path,
                    exported_files=exported_files,
                    mime_parse_succeeded=inspection.mime_parse_succeeded,
                    mime_parts_found=inspection.mime_parts_found,
                    candidate_count=len(inspection.candidates),
                )
            )

        return results

    def _write_candidates(
        self,
        source_path: Path,
        candidates: list[TiffCandidate],
        output_dir: Path,
        log: LogFn,
    ) -> list[Path]:
        written_paths: list[Path] = []
        total = len(candidates)

        for index, candidate in enumerate(candidates, start=1):
            target_path = self._candidate_output_path(
                source_path=source_path,
                candidate=candidate,
                output_dir=output_dir,
                index=index,
                total=total,
            )
            target_path.write_bytes(candidate.data)
            written_paths.append(target_path)
            log(
                f"Exported TIFF to {target_path} "
                f"(source={candidate.source_label}, content-type={candidate.content_type}, "
                f"bytes={candidate.payload_size}, header_offset={candidate.start_offset}, "
                f"note={candidate.extraction_note or 'n/a'})"
            )

        return written_paths

    def _candidate_output_path(
        self,
        *,
        source_path: Path,
        candidate: TiffCandidate,
        output_dir: Path,
        index: int,
        total: int,
    ) -> Path:
        source_stem = source_path.stem
        if total == 1:
            suggested_stem = self._suggested_stem(candidate.filename)
            if suggested_stem and suggested_stem != source_stem:
                filename = f"{source_stem}_{suggested_stem}.tif"
            else:
                filename = f"{source_stem}.tif"
        else:
            filename = f"{source_stem}_part{index:02d}.tif"
        return self._ensure_unique(output_dir / filename)

    def _suggested_stem(self, suggested_filename: str | None) -> str | None:
        if not suggested_filename:
            return None
        stem = Path(suggested_filename).stem.strip()
        if not stem:
            return None
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in stem)

    def _ensure_unique(self, path: Path) -> Path:
        if not path.exists():
            return path

        counter = 2
        while True:
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1
