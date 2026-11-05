import io
import tarfile
import tempfile
from pathlib import Path
from unittest import TestCase
from scripts.benchmark_candidates import validate_cases,extract_evidence


class CandidateCollectionTests(TestCase):
    def test_custom_cases_keep_korean_and_validate_before_dispatch(self):
        validate_cases([{'id':'case','prompt':'악어 머리','criteria':['악어']}])
        with self.assertRaises(ValueError):validate_cases([])
        with self.assertRaises(ValueError):validate_cases([{'id':'x','prompt':'x','criteria':[123]}])

    def test_archive_cannot_overwrite_outside_evidence(self):
        for name in ['../outside','/absolute','worker.env']:
            data=io.BytesIO()
            with tarfile.open(fileobj=data,mode='w:gz') as tar:
                item=tarfile.TarInfo(name);item.size=1;tar.addfile(item,io.BytesIO(b'x'))
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):extract_evidence(data.getvalue(),Path(folder))
