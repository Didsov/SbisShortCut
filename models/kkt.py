from dataclasses import dataclass


@dataclass
class LicenseData:
    finish_fs_day: str | None
    fs_close: bool


@dataclass
class Counterparty:
    inn: str | None
    kpp: str | None
    name: str | None
    legal_address: str | None
    actual_address: str | None


@dataclass
class KKT:
    id: str | None
    reg_id: str | None
    manufacturer_number: str | None
    name: str | None
    model: str | None
    active: bool
    status: str | None
    address: str | None
    timezone: str | None
    company_id: str | None
    inn: str | None
    kpp: str | None
    number: str | None
    ofd_end_date: str | None
    license: LicenseData
    counterparty: Counterparty
    raw: dict

    @property
    def organization(self) -> str | None:
        return self.counterparty.name

    def to_dict(self):
        return {
            "id": self.id,
            "reg_id": self.reg_id,
            "manufacturer_number": self.manufacturer_number,
            "name": self.name,
            "model": self.model,
            "active": self.active,
            "status": self.status,
            "address": self.address,
            "timezone": self.timezone,
            "company_id": self.company_id,
            "inn": self.inn,
            "kpp": self.kpp,
            "number": self.number,
            "ofd_end_date": self.ofd_end_date,
            "license": {
                "finish_fs_day": self.license.finish_fs_day,
                "fs_close": self.license.fs_close,
            },
            "counterparty": {
                "inn": self.counterparty.inn,
                "kpp": self.counterparty.kpp,
                "name": self.counterparty.name,
                "legal_address": self.counterparty.legal_address,
                "actual_address": self.counterparty.actual_address,
            },
        }