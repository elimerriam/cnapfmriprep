from __future__ import annotations

from pathlib import Path

from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

from seventprep.dicom import inventory_dicom_series, validated_dicom_fields


def _write_test_dicom(path: Path) -> None:
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = MRImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    dataset = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.SOPClassUID = file_meta.MediaStorageSOPClassUID
    dataset.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.Modality = "MR"
    dataset.SeriesNumber = 7
    dataset.SeriesDescription = "BOLD_MAG"
    dataset.ProtocolName = "BOLD_MAG"
    dataset.Manufacturer = "Siemens"
    dataset.ManufacturerModelName = "TestModel"
    dataset.EchoNumbers = 1
    dataset.Rows = 8
    dataset.Columns = 8
    dataset.is_little_endian = True
    dataset.is_implicit_VR = False
    dataset.save_as(str(path), write_like_original=False)


def test_inventory_field_keywords_are_valid() -> None:
    fields = validated_dicom_fields()
    assert "EchoNumbers" in fields
    assert "ManufacturerModelName" in fields
    assert "EchoNumber" not in fields
    assert "ManufacturersModelName" not in fields


def test_inventory_reads_part10_dicom(tmp_path: Path) -> None:
    dicom_path = tmp_path / "image001.dcm"
    _write_test_dicom(dicom_path)

    rows = inventory_dicom_series(tmp_path)

    assert len(rows) == 1
    assert rows[0]["SeriesDescription"] == "BOLD_MAG"
    assert rows[0]["ManufacturerModelName"] == "TestModel"
    assert rows[0]["EchoNumbers"] == "1"
    assert rows[0]["NumberOfFiles"] == 1
    assert rows[0]["Part10Files"] == 1
    assert rows[0]["ForcedReadFiles"] == 0
