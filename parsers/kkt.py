import json
import re
from datetime import date, datetime

from models.kkt import (
    KKT,
    LicenseData,
    Counterparty
)


def extract_ofd_end_date(
    registry: dict | None = None,
    detail: dict | None = None,
) -> str | None:
    """Извлекает срок ОФД из известных вариантов ответа СБИС."""
    registry = registry if isinstance(registry, dict) else {}
    detail = detail if isinstance(detail, dict) else {}

    license_data = registry.get("LicenseData")
    license_info = detail.get("license_info")

    if not isinstance(license_data, dict):
        license_data = {}

    if not isinstance(license_info, dict):
        license_info = {}

    candidates = (
        license_data.get("finish_license_day"),
        license_data.get("end_license_date"),
        license_info.get("end_license_date"),
        license_info.get("finish_license_day"),
    )

    for value in candidates:
        if value is None:
            continue

        normalized = str(value).strip()

        if normalized:
            return normalized

    return None


def extract_software_version(detail: dict | None = None) -> str | None:
    """Извлекает версию ПО из записи SoftwareInfo ответа KKT.Read."""
    detail = detail if isinstance(detail, dict) else {}
    software_info = detail.get("SoftwareInfo")

    if not isinstance(software_info, (dict, list, tuple)):
        normalized = str(software_info).strip() if software_info is not None else ""
        return normalized or None

    version_keys = {
        "currentversion",
        "version",
        "softwareversion",
        "softwareversionnumber",
        "firmwareversion",
        "версия",
        "версияпо",
        "версияпрошивки",
        "номерверсии",
    }

    def shown_date(value) -> str | None:
        normalized = str(value).strip() if value is not None else ""
        if not normalized:
            return None
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                parsed = date.fromisoformat(normalized[:10])
            except ValueError:
                return normalized
        return parsed.strftime("%d.%m.%y")

    def find_version(value) -> str | None:
        if isinstance(value, dict):
            normalized_fields = {
                re.sub(r"[^a-zа-я0-9]", "", str(key).lower()): nested_value
                for key, nested_value in value.items()
            }
            current_version = normalized_fields.get("currentversion")
            normalized_current_version = (
                str(current_version).strip()
                if current_version is not None
                else ""
            )
            if normalized_current_version:
                current_date = shown_date(normalized_fields.get("currentversiondate"))
                return (
                    f"{normalized_current_version} от {current_date}"
                    if current_date
                    else normalized_current_version
                )
            for key, nested_value in value.items():
                normalized_key = re.sub(r"[^a-zа-я0-9]", "", str(key).lower())
                if normalized_key in version_keys:
                    normalized_value = (
                        str(nested_value).strip()
                        if nested_value is not None
                        else ""
                    )
                    if normalized_value:
                        return normalized_value
            for nested_value in value.values():
                found = find_version(nested_value)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for nested_value in value:
                found = find_version(nested_value)
                if found:
                    return found
        return None

    return find_version(software_info)

def parse_used_for(value) -> dict:
    if isinstance(value, dict):
        return value

    if not value:
        return {}

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}

def parse_kkt(data):

    license_data = LicenseData(

        finish_fs_day=data.get(
            "FSEndDate"
        ),

        fs_close=data.get(
            "FSIsClose",
            False
        )

    )


    counterparty = Counterparty(

        inn=data.get(
            "ИНН"
        ),

        kpp=data.get(
            "КПП"
        ),

        name=data.get(
            "Название"
        ),

        legal_address=data.get(
            "АдресЮридический"
        ),

        actual_address=data.get(
            "АдресФактический"
        )

    )


    model = data.get(
        "kktModel",
        {}
    )

    used_for = parse_used_for(
        data.get("ГдеИспользуется")
    )



    return KKT(

        id=data.get(
            "@ККМ"
        ),

        reg_id=data.get(
            "НомерРегистрационный"
        ),

        manufacturer_number=data.get(
            "НомерПроизводителя"
        ),


        name=data.get(
            "НазваниеККМ"
        ),


        model=data.get(
            "ОборудованиеНазвание"
        ),


        active=data.get(
            "Действующая",
            False
        ),


        status=data.get(
            "СтатусРегистрацииФНС"
        ),


        address=data.get(
            "salespoint_address"
        ) or data.get(
            "АдресФактический"
        ) or data.get(
            "Адрес"
        ),


        timezone=data.get(
            "ЧасовойПояс"
        ),


        company_id=data.get(
            "Company"
        ),


        inn=data.get(
            "ИНН"
        ),


        kpp=data.get(
            "КПП"
        ),


        number=used_for.get(
            "old_fn"
        ),

        ofd_end_date=extract_ofd_end_date(
            detail=data,
        ),


        license=license_data,


        counterparty=counterparty,


        raw=data

    )
