from decoder import SBISDecoder
from sbis_client import SBISClient


def get_contractor_accounts(
    contractor_id: int,
    only_active: bool = True,
    page_size: int = 100,
    client: SBISClient | None = None,
) -> list[dict]:

    client = client or SBISClient()
    decoder = SBISDecoder()

    accounts: list[dict] = []
    page = 0

    while True:

        raw = client.call(
            "AccountContractor.List",
            {
                "Фильтр": {
                    "d": [
                        contractor_id,
                        only_active,
                    ],
                    "s": [
                        {
                            "t": "Число целое",
                            "n": "ContractorId",
                        },
                        {
                            "t": "Логическое",
                            "n": "OnlyActive",
                        },
                    ],
                    "_type": "record",
                    "f": 0,
                },

                "Сортировка": None,

                "Навигация": {
                    "d": [
                        True,
                        page_size,
                        page,
                    ],
                    "s": [
                        {
                            "t": "Логическое",
                            "n": "ЕстьЕще",
                        },
                        {
                            "t": "Число целое",
                            "n": "РазмерСтраницы",
                        },
                        {
                            "t": "Число целое",
                            "n": "Страница",
                        },
                    ],
                    "_type": "record",
                    "f": 0,
                },

                "ДопПоля": [
                    "AccountInfo",
                    "TransportState",
                ],
            },
        )

        if "error" in raw:
            raise RuntimeError(
                raw["error"].get(
                    "details",
                    raw["error"].get("message", "Ошибка SBIS"),
                )
            )

        raw_result = raw.get("result")

        if not raw_result:
            break

        decoded_page = decoder.decode(raw_result)

        if not isinstance(decoded_page, list):
            raise TypeError(
                "AccountContractor.List вернул не список."
            )

        accounts.extend(decoded_page)

        # В сыром recordset поле n обозначает наличие следующей страницы.
        has_more = bool(raw_result.get("n", False))

        if not has_more:
            break

        page += 1

    # Удаляем возможные дубликаты AccountId
    unique: dict[int, dict] = {}

    for account in accounts:

        account_id = (
            account.get("AccountId")
            or (account.get("AccountInfo") or {}).get("AccountId")
        )

        if account_id is None:
            continue

        unique[int(account_id)] = account

    return list(unique.values())
