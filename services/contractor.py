from decoder import SBISDecoder
from sbis_client import SBISClient

CONTRACTOR_URL = (
    "https://online.sbis.ru/"
    "service/?x_version=26.3248-150.3"
)


def get_contractor_by_inn(
    inn: str,
    kpp: str | None = None,
    country_code: str = "643",
    client: SBISClient | None = None,
) -> dict:

    client = client or SBISClient()

    raw = client.call(
        method="BillingContractor.ReadCard",
        params={
            "Requisites": {
                "d": [
                    inn,
                    kpp,
                    country_code,
                    None,
                ],
                "s": [
                    {
                        "t": "Строка",
                        "n": "INN",
                    },
                    {
                        "t": "Строка",
                        "n": "KPP",
                    },
                    {
                        "t": "Строка",
                        "n": "CountryCode",
                    },
                    {
                        "t": "Число целое",
                        "n": "BillingExtId",
                    },
                ],
                "_type": "record",
                "f": 0,
            }
        },
        url=CONTRACTOR_URL,
    )

    if "error" in raw:
        raise RuntimeError(
            raw["error"].get(
                "details",
                raw["error"].get("message", "Ошибка SBIS"),
            )
        )

    result = raw.get("result")

    if not result:
        raise LookupError(
            f"Контрагент с ИНН {inn} не найден."
        )

    contractor = SBISDecoder().decode(result)

    if not contractor.get("@Лицо"):
        raise LookupError(
            f"Для ИНН {inn} не получен ContractorId."
        )

    return contractor

def get_contractor_card(
    inn: str,
    kpp: str | None = None,
    client: SBISClient | None = None,
) -> dict | None:
    """
    Получает карточку контрагента по ИНН.
    """

    client = client or SBISClient()

    params = {
        "Requisites": {
            "d": [
                inn,
                kpp,
                "643",
                None,
            ],
            "s": [
                {
                    "t": "Строка",
                    "n": "INN",
                },
                {
                    "t": "Строка",
                    "n": "KPP",
                },
                {
                    "t": "Строка",
                    "n": "CountryCode",
                },
                {
                    "t": "Число целое",
                    "n": "BillingExtId",
                },
            ],
            "_type": "record",
            "f": 0,
        }
    }


    response = client.call(
        method="BillingContractor.ReadCard",
        params=params,
        url=CONTRACTOR_URL,
    )


    if "error" in response:
        raise RuntimeError(
            response["error"]
        )


    result = response["result"]["d"]


    return {
        "contractor_id": result[0],
        "inn": result[1],
        "kpp": result[2],
        "name": result[4],
        "point_id": result[5],
        "legal_address": result[11],
        "region": result[15],
        "account_id": result[21],
    }

def get_billing_contractor_id(
    inn: str,
    kpp: str | None = None,
    client: SBISClient | None = None,
) -> int:

    contractor = get_contractor_by_inn(
        inn=inn,
        kpp=kpp,
        client=client,
    )

    contractor_id = contractor.get("@Лицо")

    if not contractor_id:
        raise LookupError(
            f"Для ИНН {inn} не найден Billing ContractorId"
        )

    return contractor_id
