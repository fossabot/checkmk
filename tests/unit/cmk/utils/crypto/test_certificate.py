#!/usr/bin/env python3
# Copyright (C) 2023 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from contextlib import AbstractContextManager
from contextlib import nullcontext as does_not_raise
from datetime import datetime
from pathlib import Path

import cryptography.x509 as x509
import pytest
from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from cmk.utils.crypto.certificate import (
    Certificate,
    CertificatePEM,
    CertificateSigningRequest,
    CertificateWithPrivateKey,
    InvalidExpiryError,
    PersistedCertificateWithPrivateKey,
    X509Name,
)
from cmk.utils.crypto.keys import InvalidSignatureError, PrivateKey
from cmk.utils.crypto.password import Password
from cmk.utils.crypto.types import InvalidPEMError


def test_generate_self_signed(self_signed_cert: CertificateWithPrivateKey) -> None:
    assert (
        self_signed_cert.public_key
        == self_signed_cert.certificate.public_key
        == self_signed_cert.private_key.public_key
    )

    assert self_signed_cert.certificate._is_self_signed()
    self_signed_cert.certificate.verify_is_signed_by(self_signed_cert.certificate)

    assert "TestGenerateSelfSigned" == self_signed_cert.certificate.common_name
    assert self_signed_cert.certificate.organization_name is not None
    assert "Checkmk Site" in self_signed_cert.certificate.organization_name


@pytest.mark.parametrize(
    "time_offset,allowed_drift,expectation",
    [
        (
            relativedelta(hours=-5),  # It's 5 hours BEFORE cert creation.
            None,  # The default 2 hours slack is not enough.
            pytest.raises(InvalidExpiryError, match="not yet valid"),
        ),
        (relativedelta(hours=+5), None, pytest.raises(InvalidExpiryError, match="expired")),
        (
            # It's now 5 hours after cert creation, so the cert has expired 3 hours ago.
            relativedelta(hours=+5),
            relativedelta(hours=+5),  # But we allow 5 hours drift.
            does_not_raise(),
        ),
        (relativedelta(minutes=+10), None, does_not_raise()),
    ],
)
def test_verify_expiry(
    self_signed_cert: CertificateWithPrivateKey,
    time_offset: relativedelta,
    allowed_drift: relativedelta,
    expectation: AbstractContextManager,
) -> None:
    # time_offset is the time difference to (mocked) certificate creation
    # allowed_drift is the tolerance parameter of verify_expiry (defaults to 2 hours for None)
    #
    # We assume self_signed_cert is valid for 2 hours. Otherwise the test will not work.
    #
    with freeze_time(self_signed_cert.certificate.not_valid_before + time_offset):
        with expectation:
            self_signed_cert.certificate.verify_expiry(allowed_drift)


@pytest.mark.parametrize(
    "when,expected_days_remaining",
    [
        (relativedelta(seconds=0), 0),  # in 2 hours
        (relativedelta(days=-1), 1),  # yesterday it was valid for another day
        (relativedelta(months=-12), 365),  # 2022 was not a leap year
        (relativedelta(hours=+2), 0),  # expires right now
        (relativedelta(hours=+4), -1),  # today but rounded "down" to a day ago
    ],
)
def test_days_til_expiry(
    self_signed_cert: CertificateWithPrivateKey,
    when: relativedelta,
    expected_days_remaining: relativedelta,
) -> None:
    with freeze_time(self_signed_cert.certificate.not_valid_before + when):
        assert self_signed_cert.certificate.days_til_expiry() == expected_days_remaining


def test_write_and_read(tmp_path: Path, self_signed_cert: CertificateWithPrivateKey) -> None:
    cert_path = tmp_path / "cert.crt"
    key_path = tmp_path / "key.pem"
    password = Password("geheim")

    PersistedCertificateWithPrivateKey.persist(self_signed_cert, cert_path, key_path, password)

    assert key_path.read_bytes().splitlines()[0] == b"-----BEGIN ENCRYPTED PRIVATE KEY-----"
    assert cert_path.read_bytes().splitlines()[0] == b"-----BEGIN CERTIFICATE-----"

    loaded = PersistedCertificateWithPrivateKey.read_files(cert_path, key_path, password)

    assert loaded.certificate.serial_number == self_signed_cert.certificate.serial_number

    # mypy doesn't find most attributes of cryptography's RSAPrivateKey as it seems
    loaded_nums = loaded.private_key._key.private_numbers()  # type: ignore[attr-defined]
    orig_nums = self_signed_cert.private_key._key.private_numbers()  # type: ignore[attr-defined]
    assert loaded_nums == orig_nums


def test_loading_combined_file_content(self_signed_cert: CertificateWithPrivateKey) -> None:
    pw = Password("unittest")
    with pytest.raises(InvalidPEMError, match="Could not find certificate"):
        CertificateWithPrivateKey.load_combined_file_content("", None)

    with pytest.raises(InvalidPEMError, match="Unable to load certificate."):
        CertificateWithPrivateKey.load_combined_file_content(
            "-----BEGIN CERTIFICATE-----a-----END CERTIFICATE-----", None
        )

    file_content = self_signed_cert.certificate.dump_pem().str
    with pytest.raises(InvalidPEMError, match="Could not find private key"):
        CertificateWithPrivateKey.load_combined_file_content(file_content, None)
    with pytest.raises(InvalidPEMError, match="Could not find encrypted private key"):
        CertificateWithPrivateKey.load_combined_file_content(file_content, pw)

    assert (
        CertificateWithPrivateKey.load_combined_file_content(
            file_content + "\n" + self_signed_cert.private_key.dump_pem(None).str, None
        )
        .certificate.dump_pem()
        .str
        == self_signed_cert.certificate.dump_pem().str
    )
    assert (
        CertificateWithPrivateKey.load_combined_file_content(
            file_content + "\n" + self_signed_cert.private_key.dump_pem(pw).str, pw
        )
        .certificate.dump_pem()
        .str
        == self_signed_cert.certificate.dump_pem().str
    )


def test_verify_is_signed_by() -> None:
    # These were generated by hand.

    # Currently the certificate expiry is not checked. If this will be checked
    # one day, we should fix the time here.
    ca_pem = CertificatePEM(
        "\n".join(
            [
                "-----BEGIN CERTIFICATE-----",
                "MIICBDCCAW2gAwIBAgIUNu5U+5nbZYtaG90vJ46ozhgTp/IwDQYJKoZIhvcNAQEL",
                "BQAwLDEYMBYGA1UEAwwPU29tZSB0ZXN0aW5nIENBMRAwDgYDVQQKDAdUZXN0aW5n",
                "MB4XDTIzMDMwMzEwMTcyMFoXDTIzMDYwMTEwMTcyMFowLDEYMBYGA1UEAwwPU29t",
                "ZSB0ZXN0aW5nIENBMRAwDgYDVQQKDAdUZXN0aW5nMIGfMA0GCSqGSIb3DQEBAQUA",
                "A4GNADCBiQKBgQC7JTszgqpicT/qgnhYg10rYrGb0R/oYTjMG26RcSNw18ooSwYT",
                "1syLmV9ifXZ3GRyq08gg8SQgpUfgTwkySIvDJE5lpvk6yN3Ss3QACFKAOiaY79rj",
                "iiIBuKN+Woor4cgJG6KbK3uDMyPNezsjmZEiy5g5DdQUsAFN9CnVIWH0XQIDAQAB",
                "oyMwITAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBxjANBgkqhkiG9w0B",
                "AQsFAAOBgQChNpbrgxHFoyoSsjkcVaZHwK0nMgXzshaqeqciiDvEq3dS2+leFmcq",
                "KRB5AiLvdDQlWaJrFtdmfQs3uYRdJFmB9fIWvWdoUXRqcWphXq5+6IxWIsAF3Z6M",
                "Bbepnq9rp3OltabW0ux45qry09RFAVJA4eh+jOqKBMFNYrkVNGbgTg==",
                "-----END CERTIFICATE-----",
            ]
        )
    )
    cert_pem = CertificatePEM(
        "\n".join(
            [
                "-----BEGIN CERTIFICATE-----",
                "MIIB+DCCAWGgAwIBAgIUJfamCnTW8nHHswQf+Th1atxs5C4wDQYJKoZIhvcNAQEN",
                "BQAwLDEYMBYGA1UEAwwPU29tZSB0ZXN0aW5nIENBMRAwDgYDVQQKDAdUZXN0aW5n",
                "MB4XDTIzMDMwMzEwMTcyMFoXDTIzMDYwMTEwMTcyMFowIzEPMA0GA1UEAwwGc2ln",
                "bmVkMRAwDgYDVQQKDAdUZXN0aW5nMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKB",
                "gQCxByXer6BpmXAkqdoqyGtf6fSuVFLN+k01CNRwhd8LOu4F7MVC9ZO5IxuHYfWr",
                "89TXZ2KaF+4r/Pxa89/k7pnwIwoJI16xc7cY6Lcba9/6GJngFYvD6ThNKio9GztA",
                "4vJS2OBF/tfZCwEW3s3Chfw3d5/ggRtiOR80/PiHKElxXwIDAQABoyAwHjAMBgNV",
                "HRMBAf8EAjAAMA4GA1UdDwEB/wQEAwID+DANBgkqhkiG9w0BAQ0FAAOBgQCvquV7",
                "cTkaozgnvwsgkyU8+qSeWtJ5mGJ700ASjCvBd7ZabC/efVAmeNhZDk4dHfl7mk/r",
                "vrSfdEuFFCOSo8rCNgFNBGIVvtu8ks1Viuq2zpwsE26JMT1dKA+0DdtDpOzSf7MV",
                "8k2/bFIyzFv9CEw43B6UX4QC2nNfWJIu1YkV2g==",
                "-----END CERTIFICATE-----",
            ]
        )
    )
    other_pem = CertificatePEM(
        "\n".join(
            [
                "-----BEGIN CERTIFICATE-----",
                "MIIB+DCCAWGgAwIBAgIUaFZY5XpToxJGKwy2XgkHywlqivkwDQYJKoZIhvcNAQEN",
                "BQAwLDEYMBYGA1UEAwwPU29tZSB0ZXN0aW5nIENBMRAwDgYDVQQKDAdUZXN0aW5n",
                "MB4XDTIzMDMwMzEwMjAwN1oXDTIzMDYwMTEwMjAwN1owIzEPMA0GA1UEAwwGc2ln",
                "bmVkMRAwDgYDVQQKDAdUZXN0aW5nMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKB",
                "gQC8X9pCePQ2IVftGsc2KSG6o5NiM8WIubWw7BRLUOUEaqN63fmx5aRBhtbZk5ig",
                "mX8a9v6epaTdf6NRcHdOKuh1U5QSE1+XO/N8IAKjBdLFqZ0JHEmQIq6mc5zY4kIw",
                "tTBWk0Lshw09ZscH3YuE3LuUavdMSSHMkKINJFaGXlUUgQIDAQABoyAwHjAMBgNV",
                "HRMBAf8EAjAAMA4GA1UdDwEB/wQEAwID+DANBgkqhkiG9w0BAQ0FAAOBgQCNso9R",
                "ZRhI23EEaXMmpRqi46ViHgBQMsVGBWiBXk8LU44P5o/P+V4o9xlclm/Vs+JVPYxO",
                "e8fM3UdwRKFkw9Z17/oD7uIy1Kj+4wAJdm6A+iXRPIaQMooGFeg6cR4oPx9chPzM",
                "e9Q7tuF8u+dnFcJ2cHWAOnXGOZPQjT4W3EgUfw==",
                "-----END CERTIFICATE-----",
            ]
        )
    )
    Certificate.load_pem(cert_pem).verify_is_signed_by(Certificate.load_pem(ca_pem))
    with pytest.raises(InvalidSignatureError):
        Certificate.load_pem(other_pem).verify_is_signed_by(Certificate.load_pem(ca_pem))


def test_default_subject_alt_names(self_signed_cert: CertificateWithPrivateKey) -> None:
    """check that the self-signed cert does not come with SANs"""
    assert self_signed_cert.certificate.get_subject_alt_names() == []


@pytest.mark.parametrize(
    "sans",
    (
        ([]),
        (["foo.bar", "bar.foo"]),
    ),
)
def test_subject_alt_names(self_signed_cert: CertificateWithPrivateKey, sans: list[str]) -> None:
    """test setting and retrieval of subject-alt-names (DNS)"""
    assert (
        Certificate._create(
            subject_public_key=self_signed_cert.private_key.public_key,
            subject_name=X509Name.create(common_name="sans_test"),
            subject_alt_dns_names=sans,
            expiry=relativedelta(days=1),
            start_date=datetime.now(),
            issuer_signing_key=self_signed_cert.private_key,
            issuer_name=X509Name.create(common_name="sans_test"),
        ).get_subject_alt_names()
        == sans
    )


def test_sign_csr(self_signed_cert: CertificateWithPrivateKey, rsa_key: PrivateKey) -> None:
    csr = CertificateSigningRequest.create(
        subject_name=X509Name.create(common_name="csr_test", organization_name="csr_test_org"),
        subject_private_key=rsa_key,
    )

    with freeze_time(self_signed_cert.certificate.not_valid_before):
        new_cert = self_signed_cert.sign_csr(csr, expiry=relativedelta(days=1))

    assert new_cert.not_valid_before == self_signed_cert.certificate.not_valid_before
    assert (
        new_cert.not_valid_after
        == self_signed_cert.certificate.not_valid_before + relativedelta(days=1)
    )

    new_cert.verify_is_signed_by(self_signed_cert.certificate)
    assert (
        new_cert.public_key == rsa_key.public_key
    ), "The public key in the certificate matches the private key in the CSR"
    assert (
        new_cert.issuer == self_signed_cert.certificate.subject
    ), "The issuer of the new certificate is the self_signed_cert"


def test_may_sign_certificates() -> None:
    pem = CertificatePEM(
        # openssl req -x509 -newkey rsa:1024 -sha256 -nodes -keyout key.pem -out cert.pem -days 365
        """-----BEGIN CERTIFICATE-----
MIIC3jCCAkegAwIBAgIUFfy37IHOcIANa5IVx93DEmYO0zowDQYJKoZIhvcNAQEL
BQAwgYAxCzAJBgNVBAYTAkRFMRAwDgYDVQQIDAdCYXZhcmlhMSEwHwYDVQQKDBhJ
bnRlcm5ldCBXaWRnaXRzIFB0eSBMdGQxGDAWBgNVBAMMD2NoZWNrbWtfdGVzdF9j
YTEiMCAGCSqGSIb3DQEJARYTdGVzdGluZ0BleGFtcGxlLmNvbTAeFw0yMzExMTcx
NTIxMjhaFw0yNDExMTYxNTIxMjhaMIGAMQswCQYDVQQGEwJERTEQMA4GA1UECAwH
QmF2YXJpYTEhMB8GA1UECgwYSW50ZXJuZXQgV2lkZ2l0cyBQdHkgTHRkMRgwFgYD
VQQDDA9jaGVja21rX3Rlc3RfY2ExIjAgBgkqhkiG9w0BCQEWE3Rlc3RpbmdAZXhh
bXBsZS5jb20wgZ8wDQYJKoZIhvcNAQEBBQADgY0AMIGJAoGBANR+1oqFEX52v9ZJ
xyEh93DnV5Zp9H3scxeFnnjpK0epFFCM/J6yiggws845+MYv7tvVM2rQHO8ud/2i
fpe5yuqcWtFfgm9UDHqsndiANyFKjkJ6PDfPLeyWmmXZuoHMPK3He/5usP6ovxYb
5OfqM2ZwleMoMSrVmjGHv5rfvYdXAgMBAAGjUzBRMB0GA1UdDgQWBBSQSbT3kTAg
QKpT/KrKFerYoxe17DAfBgNVHSMEGDAWgBSQSbT3kTAgQKpT/KrKFerYoxe17DAP
BgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4GBABmlbyhZeb7sx3BH3C0h
JPEGQcTEa+Xvh3EFz2mMldkkZP1hXqkiFMTHZGJ2Q3HXrUJ/jFUGUKRnWAmxUfu/
/pUPP2kOchlsjMPP6JCeZLsB6N/3fIRqHhamI5jr6KyBB0eJnR7QgB3sG8liPFbQ
JxDm8nhVOD3txg6wadiqhhdB
-----END CERTIFICATE-----
"""
    )
    cert = Certificate.load_pem(pem)
    with pytest.raises(x509.ExtensionNotFound):
        # The tested cert does not set the key usage extension at all,
        # this should not prevent certificate signing.
        # Only if the extension is there but the key_cert_sign bit is missing the cert should not be
        # used for signing.
        # This is a regression test.
        cert._cert.extensions.get_extension_for_class(x509.KeyUsage)

    assert cert.may_sign_certificates()
