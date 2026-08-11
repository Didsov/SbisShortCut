from dataclasses import dataclass
import json
from decoder import SBISDecoder
from sbis_client import SBISClient
from services.console_output import print_event
from services.performance import record_operation_retry
import time

def build_registry_position(
    row_id: int,
) -> dict:
    return {
        "d": [
            int(row_id)
        ],
        "s": [
            {
                "t": "Число целое",
                "n": "KKTRowId",
            }
        ],
        "_type": "record",
        "f": 1,
    }


def extract_registry_next_position(
    raw_result: dict,
) -> dict | None:
    """
    Извлекает позицию следующей страницы RegKKT.Registry.

    В result["m"] находится запись nextPosition,
    внутри которой лежит recordset:

        id | nav_result

    Для корневого списка используется строка с id=None.
    """

    metadata = raw_result.get("m")

    if not isinstance(metadata, dict):
        return None

    metadata_schema = metadata.get("s") or []
    metadata_values = metadata.get("d") or []

    next_position_index = next(
        (
            index
            for index, field in enumerate(metadata_schema)
            if field.get("n") == "nextPosition"
        ),
        None,
    )

    if next_position_index is None:
        return None

    if next_position_index >= len(metadata_values):
        return None

    navigation_recordset = metadata_values[
        next_position_index
    ]

    if not isinstance(navigation_recordset, dict):
        return None

    rows = navigation_recordset.get("d") or []
    schema = navigation_recordset.get("s") or []

    id_index = next(
        (
            index
            for index, field in enumerate(schema)
            if field.get("n") == "id"
        ),
        None,
    )

    nav_result_index = next(
        (
            index
            for index, field in enumerate(schema)
            if field.get("n") == "nav_result"
        ),
        None,
    )

    if (
        id_index is None
        or nav_result_index is None
    ):
        return None

    # Сначала ищем навигацию корневого списка:
    # [None, [1]]
    for row in rows:
        if not isinstance(row, list):
            continue

        if max(id_index, nav_result_index) >= len(row):
            continue

        row_id = row[id_index]
        nav_result = row[nav_result_index]

        if row_id is not None:
            continue

        row_position = extract_registry_row_id(
            nav_result
        )

        if row_position is not None:
            return build_registry_position(
                row_position
            )

    return None


def extract_registry_row_id(
    nav_result,
) -> int | None:
    """
    Извлекает KKTRowId из значений вида:

        [1]
        [[1]]
        1
        "1"
    """

    value = nav_result

    while (
        isinstance(value, list)
        and len(value) == 1
    ):
        value = value[0]

    try:
        return int(value)
    except (TypeError, ValueError):
        return None

@dataclass
class KKTRegistryPage:
    items: list[dict]
    has_more: bool
    next_position: dict | None


class PartialRegistryError(RuntimeError):
    """Registry оборвался, но часть уникальных строк уже получена.

    Вызывающий код обязан обработать ``items`` и сохранить найденные ККТ,
    однако не должен считать обход аккаунта полностью завершённым.
    """

    def __init__(
        self,
        message: str,
        items: list[dict],
    ) -> None:
        super().__init__(message)
        self.items = items


def is_request_timeout(error: Exception) -> bool:
    message = str(error).lower()
    return "request timeout" in message or "timed out" in message


def smaller_registry_limit(current_limit: int) -> int:
    if current_limit > 10:
        return 10

    if current_limit > 5:
        return 5

    return current_limit


def get_kkt_registry_page(
    account_id: int,
    *,
    limit: int = 25,
    position: dict | None = None,
    include_archived: bool = False,
    client: SBISClient | None = None,
) -> KKTRegistryPage:

    client = client or SBISClient()
    decoder = SBISDecoder()

    response = client.call(
        "RegKKT.Registry",
        {
            "Фильтр": {
                "d": [
                    account_id,
                    None,
                    None,
                    None,
                    include_archived,
                    None,
                    None,
                ],
                "s": [
                    {
                        "t": "Число целое",
                        "n": "AccountId",
                    },
                    {
                        "t": "Строка",
                        "n": "OnlineOwnerId",
                    },
                    {
                        "t": "Строка",
                        "n": "folderUniqueId",
                    },
                    {
                        "t": "Строка",
                        "n": "hierarchyCompositeKey",
                    },
                    {
                        "t": "Логическое",
                        "n": "includeArchived",
                    },
                    {
                        "t": "Строка",
                        "n": "problemGroupId",
                    },
                    {
                        "t": "Строка",
                        "n": "status",
                    },
                ],
                "_type": "record",
                "f": 0,
            },

            "Сортировка": None,

            "Навигация": {
                "d": [
                    "forward",
                    True,
                    limit,
                    position,
                ],
                "s": [
                    {
                        "t": "Строка",
                        "n": "Direction",
                    },
                    {
                        "t": "Логическое",
                        "n": "HasMore",
                    },
                    {
                        "t": "Число целое",
                        "n": "Limit",
                    },
                    {
                        "t": "Запись",
                        "n": "Position",
                    },
                ],
                "_type": "record",
                "f": 0,
            },

            "ДопПоля": [],
        },
    )

    if "error" in response:
        error = response["error"]

        raise RuntimeError(
            error.get("details")
            or error.get("message")
            or "Ошибка RegKKT.Registry"
        )

    raw_result = response.get("result")

    if not raw_result:
        return KKTRegistryPage(
            items=[],
            has_more=False,
            next_position=None,
        )

    items = decoder.decode(raw_result)

    if not isinstance(items, list):
        raise TypeError(
            "RegKKT.Registry вернул не список"
        )

    has_more = bool(
        raw_result.get("n", False)
    )

    next_position = extract_registry_next_position(
        raw_result
    )

    return KKTRegistryPage(
        items=items,
        has_more=has_more,
        next_position=next_position,
    )



def get_all_kkt_registry(
    account_id: int,
    *,
    limit: int = 25,
    include_archived: bool = False,
    max_pages: int = 200,
    retry_attempts: int = 5,
    client: SBISClient | None = None,
) -> list[dict]:
    """
    Загружает все страницы RegKKT.Registry.

    Возвращает уникальные элементы реестра.
    ККТ дедуплицируются по KKTId/KKTRegId,
    папки и точки — по compositeKey.

    Если сервер оборвал пагинацию после получения части данных, выбрасывает
    PartialRegistryError с уже накопленными элементами. Повтор Position без
    новых строк и неполная последняя страница без Position считаются штатным
    окончанием обхода: такое поведение встречается в RegKKT.Registry.
    """

    position = None
    page_number = 0
    effective_limit = max(1, int(limit))

    seen_positions: set[str] = set()
    unique_items: dict[tuple, dict] = {}
    while True:
        page_number += 1

        if page_number > max_pages:
            raise PartialRegistryError(
                f"Registry превысил лимит {max_pages} страниц. "
                "Сохраняем уже полученные строки, но сбор неполный.",
                list(unique_items.values()),
            )

        page = None
        last_error = None
        for attempt in range(1, retry_attempts + 1):
            try:
                page = get_kkt_registry_page(
                    account_id=account_id,
                    limit=effective_limit,
                    position=position,
                    include_archived=include_archived,
                    client=client,
                )
                break

            except Exception as error:
                last_error = error

                reduced_limit = smaller_registry_limit(effective_limit)

                if (
                    is_request_timeout(error)
                    and reduced_limit < effective_limit
                    and attempt < retry_attempts
                ):
                    previous_limit = effective_limit
                    effective_limit = reduced_limit
                    record_operation_retry("registry")
                    print_event(
                        f"[АДАПТАЦИЯ] AccountId={account_id} Registry "
                        f"страница={page_number}: Request timeout, "
                        f"Limit {previous_limit} -> {effective_limit}"
                    )
                    time.sleep(1.0)
                    continue

                print_event(
                    f"[ПОВТОР] AccountId={account_id} Registry "
                    f"страница={page_number}, попытка={attempt}/{retry_attempts}: "
                    f"{error}"
                )

                if attempt < retry_attempts:
                    record_operation_retry("registry")
                    delay = attempt * 2
                    time.sleep(delay)

        if page is None:
            raise PartialRegistryError(
                f"Не удалось получить страницу {page_number} "
                f"Registry: {last_error}. Сохраняем частичный результат.",
                list(unique_items.values()),
            )

        new_rows = 0
        new_kkt = 0

        for item in page.items:
            if not isinstance(item, dict):
                continue

            kkt_id = item.get("KKTId")
            kkt_reg_id = item.get("KKTRegId")

            if kkt_id is not None:
                if kkt_reg_id:
                    key = (
                        "kkt_reg_id",
                        str(kkt_reg_id),
                    )
                else:
                    key = (
                        "kkt_id",
                        account_id,
                        kkt_id,
                    )
            else:
                key = (
                    "node",
                    item.get("compositeKey"),
                    item.get("hierarchyCompositeKey"),
                    item.get("@Лицо"),
                    item.get("Название"),
                )

            if key in unique_items:
                continue

            unique_items[key] = item
            new_rows += 1

            if kkt_id is not None:
                new_kkt += 1




        if not page.has_more:
            break

        if page.next_position is None:
            if len(page.items) < effective_limit:
                break

            raise PartialRegistryError(
                "RegKKT.Registry сообщил HasMore=True, но не вернул "
                "следующую Position. Сохраняем частичный результат.",
                list(unique_items.values()),
            )

        position_key = json.dumps(
            page.next_position,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

        if position_key in seen_positions:
            if new_rows == 0:
                break
            # На крупных аккаунтах СБИС может несколько раз возвращать одну
            # Position, при этом постепенно добавляя новые строки. Пока есть
            # прогресс, продолжаем обход; max_pages защищает от бесконечного
            # ответа сервера.
        else:
            seen_positions.add(position_key)

        position = page.next_position

    return list(
        unique_items.values()
    )
