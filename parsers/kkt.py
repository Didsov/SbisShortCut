import json

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
