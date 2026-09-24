# `KKT.Read`: запрос и поля карточки кассы

Документ описывает внутренний read-only RPC-метод SBIS `KKT.Read`, его входные
идентификаторы и подтвержденные пути полей декодированного ответа.

Схема проверена 22 сентября 2026 года живыми вызовами 22 доступных карточек у
двух разных клиентов.
Cookie, ИНН, РНМ, номера ФН, адреса, ФИО и другие значения в документацию не
сохранялись.

## Назначение и место в цепочке

`KKT.Read` возвращает подробную карточку одной кассы. Перед вызовом необходимо
получить аккаунт и строку реестра:

```text
AccountContractor.List
  `-- AccountId
        `-- RegKKT.Registry
              `-- KKTId + KKTRegId
                    `-- KKT.Read
```

Самостоятельно искать кассу по одному ИНН метод не умеет.

## Endpoint

```text
POST https://online.sbis.ru/billing_public/service/?x_version=<актуальная версия>
Method: KKT.Read
X-Originalmethodname: S0tULlJlYWQ=
```

Версия endpoint и Saby-заголовков должна соответствовать актуальному
веб-клиенту. Авторизация выполняется Cookie текущей учетной записи и не меняет
ее права.

## Запрос

```json
{
  "jsonrpc": "2.0",
  "protocol": 7,
  "method": "KKT.Read",
  "params": {
    "Params": {
      "d": [12345678, 987654, "0000000000000000"],
      "s": [
        {"t": "Число целое", "n": "AccountId"},
        {"t": "Число целое", "n": "KKTId"},
        {"t": "Строка", "n": "KKTRegId"}
      ],
      "_type": "record",
      "f": 0
    }
  },
  "id": 1
}
```

| Параметр | Источник | Назначение |
|---|---|---|
| `AccountId` | `AccountContractor.List.AccountId` | Биллинговый аккаунт, в котором находится касса. |
| `KKTId` | `RegKKT.Registry.KKTId` | Внутренний числовой ID кассы. |
| `KKTRegId` | `RegKKT.Registry.KKTRegId` | Регистрационный номер ККТ (РНМ). |

Все три значения нужно брать из одной ветки `AccountId -> Registry row`. Один
РНМ может встречаться в нескольких аккаунтах, поэтому нельзя подменять
`AccountId` значением из другой строки.

## Формат ответа

Успешный ответ содержит типизированную запись в `result`:

```json
{
  "jsonrpc": "2.0",
  "result": {
    "d": [],
    "s": [],
    "_type": "record",
    "f": 0
  },
  "id": 1
}
```

После позиционного сопоставления `result.d[i]` с `result.s[i].n` получается
обычный словарь. В проекте это делает `SBISDecoder`.

Проверенный ответ содержал верхнеуровневые группы:

- идентификаторы и регистрационные сведения;
- реквизиты владельца;
- модель и состояние кассы;
- `fiscalItem` — установленный фискальный накопитель;
- `MonitoringData` и `ShiftData` — мониторинг и смена;
- `license_info` и `serviceSettings` — обслуживание ОФД;
- `SoftwareInfo` — версия ПО;
- `used_for` / `ГдеИспользуется` — параметры применения кассы;
- `Owner`, `headChief`, `Contract` — персональные и договорные данные.

Последние три группы нельзя без необходимости сохранять в диагностических
дампах: они могут содержать ФИО, документы, адреса и договорные реквизиты.

## Точные пути требуемых полей

### Сводная таблица

| Данные | Основной путь после декодирования | Резервный путь | Проверка |
|---|---|---|---|
| ИНН владельца | `ИНН` | `serviceSettings.inn` | `ИНН` присутствовал в 22 из 22 карточек. |
| `KKTId` | `@ККМ` | `fiscalItem.ККМ` | `@ККМ` совпал с `KKTId` запроса. |
| `KKTRegId` / РНМ | `НомерРегистрационный` | `MonitoringData.KKTRegId` | Основное поле совпало с `KKTRegId` запроса. |
| Номер ФН | `fiscalItem.НомерРегистрационный` | `used_for.old_fn` | Основное поле заполнено в 19 из 22 карточек. |
| Модель ФН | `fiscalItem.Модель` | отсутствует подтвержденный резервный путь | Заполнено в 19 из 22 карточек. |
| Окончание ФН | `FSEndDate` | отсутствует в самом `KKT.Read` | Заполнено в 19 из 22 карточек. |
| Оператор/настройка ОФД | `serviceSettings.ofdName` | `RegKKT.Registry.OperatorName` | Поле заполнено, но не доказывает, что касса обслуживается этим ОФД. |
| Окончание ОФД | `license_info.end_license_date` | `license_info.activation_end_date` требует отдельной трактовки | Основное поле заполнено в 18 из 22 карточек. |
| Признак действующей кассы | `Действующая` | `СтатусРегистрацииФНС` | `Действующая` может быть `null`; числовой статус требует таблицы enum. |
| Закрытие ФН | `FSIsClose` | `MonitoringData.FSClosed` | Это состояние ФН, а не архивность кассы. |

### ИНН владельца

```python
owner_inn = detail.get("ИНН")
```

Перед включением результата в отчет необходимо сравнить это значение с ИНН
клиента. Если верхнее поле пусто, `serviceSettings.inn` можно использовать для
диагностики, но такое резервирование отдельно не проверялось на неполных
карточках.

### `KKTId`

```python
kkt_id = detail.get("@ККМ")
```

На проверенной карточке `@ККМ` точно совпал с `KKTId`, переданным в запрос.
Поле `KKTBillingId` также присутствует, но является другим биллинговым
идентификатором и не должно подставляться вместо `KKTId`.

### `KKTRegId`

```python
kkt_reg_id = detail.get("НомерРегистрационный")
```

На проверенной карточке поле совпало с `RegKKT.Registry.KKTRegId`. Отдельного
верхнеуровневого поля `KKTRegId` в декодированном ответе не было. В
`MonitoringData.KKTRegId` находится дополнительное представление РНМ.

### Номер и модель ФН

Основная запись:

```python
fiscal_item = detail.get("fiscalItem") or {}
fn_number = fiscal_item.get("НомерРегистрационный")
fn_model = fiscal_item.get("Модель")
```

Подтвержденная схема `fiscalItem`:

```text
@ФискальныйНакопитель
Модель
НомерРегистрационный
Действующий
ДатаОткрытия
ДатаЗакрытия
ККМ
```

Несмотря на имя `НомерРегистрационный`, внутри `fiscalItem` это заводской номер
ФН, а верхнее `НомерРегистрационный` — РНМ кассы. Их нельзя смешивать.

Резервный номер ФН:

```python
used_for = detail.get("used_for") or {}
fn_number = fn_number or used_for.get("old_fn")
```

`ГдеИспользуется` может содержать те же данные как JSON-строку; перед чтением
ее нужно декодировать через `json.loads`. Модель ФН в проверенной схеме
подтверждена только в `fiscalItem.Модель`.

### Дата окончания ФН

```python
fn_end_date = detail.get("FSEndDate")
```

Поле является верхнеуровневым. В проверенной выборке оно присутствовало тогда
же, когда была полноценная запись `fiscalItem`: 19 из 22 карточек.

Не следует выводить модель ФН из длительности до `FSEndDate`: модель берется
только из явного `fiscalItem.Модель`.

### Оператор ОФД

```python
service_settings = detail.get("serviceSettings") or {}
ofd_name = service_settings.get("ofdName")
```

Подтвержденная схема `serviceSettings`:

```text
address
inn
markingHost
markingPort
ofdName
port
regNumber
```

Для отчета обычно нужен только `ofdName`. Остальные поля содержат адрес
подключения, ИНН и технические настройки, их не следует логировать без
необходимости.

Важно: `serviceSettings.ofdName` описывает настройку сервиса/импорта и не
является надежным доказательством фактического текущего ОФД. На проверенной
карточке, которую интерфейс SBIS показывает как «Касса подключена к другому
ОФД», значение `serviceSettings.ofdName` классифицировалось как Saby, но при
этом отсутствовали `license_info`, `Contract`, `LicStatus` и Registry
`LicenseData`. Поэтому фактического стороннего оператора из одного `KKT.Read`
определить нельзя.

### Дата окончания ОФД

```python
license_info = detail.get("license_info") or {}
ofd_end_date = license_info.get("end_license_date")
```

В `license_info` подтверждены поля:

```text
activation_date
activation_end_date
deactivation_date
end_license_date
end_license_date_remain
license_type
license_is_day
license_is_check
is_trial
Dates
```

Проект трактует `end_license_date` как срок услуги ОФД. Поле
`activation_end_date` также является датой, но без дополнительного сравнения
тарифов его нельзя автоматически считать тем же сроком.

Если `KKT.Read.license_info.end_license_date` отсутствует, текущий сборщик берет
резервное значение из строки Registry:

```text
RegKKT.Registry.LicenseData.finish_license_day
RegKKT.Registry.LicenseData.end_license_date
```

Эти резервные пути относятся к `RegKKT.Registry`, а не к `KKT.Read`.

## Прикладные правила активности

Ниже зафиксирована бизнес-классификация для будущего сборщика. Она дополняет
сырые поля SBIS и не должна подменяться предположениями о числовых enum.

### 1. Запись без РНМ

Если строка Registry имеет `KKTId`, но не имеет непустого `KKTRegId`, это
незавершенная регистрация кассы: регистрацию начинали, но не довели до конца.

```python
reg_number = str(registry_item.get("KKTRegId") or "").strip()
if not reg_number:
    state = "registration_incomplete"
```

Для такой строки нельзя вызвать нормальный `KKT.Read`, потому что запрос требует
`AccountId + KKTId + KKTRegId`. В проверочном клиенте обнаружено 5 таких строк
из 14 записей Registry.

Рекомендуемое поведение:

- не считать запись действующей кассой;
- не пытаться вызывать `KKT.Read` с пустым РНМ;
- сохранить отдельную причину `registration_incomplete`;
- при необходимости показывать модель/адрес только из Registry, помечая данные
  как неполные.

### 2. Нет сроков ФН и ОФД

Если после всех fallback отсутствуют обе даты:

```text
fn_end_date  = KKT.Read.FSEndDate
ofd_end_date = KKT.Read.license_info.end_license_date
            or Registry.LicenseData.finish_license_day
            or Registry.LicenseData.end_license_date
```

кассу следует считать деактивированной для задач контроля Saby ОФД:

```python
if fn_end_date is None and ofd_end_date is None:
    state = "inactive_no_terms"
    probable_reason = "other_ofd_or_no_saby_service_data"
```

Это не означает, что касса физически выключена или снята с учета ФНС. Наиболее
вероятно, она подключена к другому ОФД, поэтому в Saby нет договора и сроков.

На проверенном примере карточка из интерфейса «подключена к другому ОФД» имела:

```text
Registry.Active = true
KKT.Read.Действующая = null
KKT.Read.СтатусРегистрацииФНС = 300
KKT.Read.service_status = null
KKT.Read.license_info = отсутствует
KKT.Read.Contract = отсутствует
KKT.Read.LicStatus = отсутствует
Registry.LicenseData = отсутствует
Registry.Contract = отсутствует
```

Поле `reasons.ofd` при этом было `false`, а `serviceSettings.ofdName` не
указывало надежно на стороннего оператора. Следовательно, ни одно из этих полей
нельзя использовать как прямой признак `other_ofd`.

Безопасная формулировка результата:

```text
Сроки Saby ОФД отсутствуют; вероятно, касса обслуживается другим ОФД.
```

### 3. Сроки закончились более трех месяцев назад

Если известные сроки давно истекли, касса также считается деактивированной для
отчета. Граница рассчитывается как три календарных месяца до даты отчета, а не
как фиксированные 90 дней.

Чтобы не деактивировать кассу, у которой один срок старый, но другой еще
действует, используется самая поздняя из известных дат:

```python
known_end_dates = [date for date in (fn_end_date, ofd_end_date) if date]
last_known_end = max(known_end_dates) if known_end_dates else None

if last_known_end is not None and last_known_end < three_month_cutoff:
    state = "inactive_expired_over_3_months"
```

Пример: для отчета на `2026-09-22` граница — `2026-06-22`. Дата окончания
раньше этой границы считается просроченной более чем на три месяца. Ровно
`2026-06-22` еще не попадает под строгое условие «больше трех месяцев».

### 4. Рекомендуемый порядок классификации

Правила применяются в следующем порядке:

```text
1. Нет KKTRegId
   -> registration_incomplete

2. Есть KKTRegId, но нет ни срока ФН, ни срока ОФД
   -> inactive_no_terms
   -> вероятно другой ОФД или нет данных сервиса Saby

3. Самая поздняя известная дата истекла более трех календарных месяцев назад
   -> inactive_expired_over_3_months

4. Есть актуальный или недавно истекший срок
   -> tracked
   -> дополнительно сохранить kkt_active и fns_status_raw
```

Статус `tracked` не равен гарантированно действующей кассе ФНС: он означает,
что касса остается актуальной для контроля сроков. Сырые поля
`Действующая` и `СтатусРегистрацииФНС` сохраняются рядом и не затираются
прикладной классификацией.

### Пример функции классификации

```python
import calendar
from datetime import date


def subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def classify_kkt(
    *,
    kkt_reg_id: str | None,
    fn_end_date: date | None,
    ofd_end_date: date | None,
    report_date: date,
) -> tuple[str, str | None]:
    if not str(kkt_reg_id or "").strip():
        return "registration_incomplete", "missing_kkt_reg_id"

    known_dates = [value for value in (fn_end_date, ofd_end_date) if value]
    if not known_dates:
        return "inactive_no_terms", "other_ofd_or_no_saby_service_data"

    cutoff = subtract_months(report_date, 3)
    if max(known_dates) < cutoff:
        return "inactive_expired_over_3_months", "terms_expired"

    return "tracked", None
```

## Состояние, закрытие и архивность

### Состояние кассы

В `KKT.Read` подтверждены:

```python
active = detail.get("Действующая")
fns_status = detail.get("СтатусРегистрацииФНС")
```

В первой проверочной группе из 11 карточек получены статусы `8`, `9` и `300`,
но их официальная
расшифровка в ответе отсутствует. `Действующая` оказалась `true` только у части
карточек и `null` у остальных. Во второй группе поле имело тип `bool` только у
3 из 11 карточек. Значение `null` нельзя приравнивать к `false`.

Поэтому безопасная модель состояния:

```python
if detail.get("Действующая") is True:
    kkt_state = "active"
elif detail.get("Действующая") is False:
    kkt_state = "inactive"
else:
    kkt_state = "unknown"

fns_status_raw = detail.get("СтатусРегистрацииФНС")
```

До получения подтвержденной таблицы кодов `СтатусРегистрацииФНС` нужно хранить
как raw enum, не переводя `8`, `9` или `300` в придуманные подписи.

### Закрытие ФН не равно закрытию кассы

Следующие поля относятся к фискальному накопителю:

```text
FSIsClose
FSDateClose
MonitoringData.FSClosed
fiscalItem.Действующий
fiscalItem.ДатаЗакрытия
```

В живой выборке встречалась карточка одновременно с:

```text
Действующая = true
FSIsClose = true
fiscalItem.Действующий = true
```

Следовательно, `FSIsClose=true` нельзя использовать как признак архивной или
закрытой кассы: это привело бы к ложной классификации.

### Архивная касса

Отдельное поле `Archived`, `Архивная` или `Закрыта` в проверенном
декодированном `KKT.Read` не обнаружено. Надежный факт попадания записи в архив
нужно получать на уровне `RegKKT.Registry`, сравнивая выдачу с
`includeArchived=false/true` или используя подтвержденное поле Registry.

Итог для реализации:

```text
KKT.Read.Действующая                  -> трехзначный active/inactive/unknown
KKT.Read.СтатусРегистрацииФНС         -> сохранить raw enum
KKT.Read.FSIsClose                    -> закрытие ФН, не кассы
RegKKT.Registry(includeArchived=true) -> источник архивных записей
```

## Обезличенный декодированный фрагмент

```json
{
  "@ККМ": "<number>",
  "НомерРегистрационный": "<text>",
  "ИНН": "<text>",
  "Действующая": true,
  "СтатусРегистрацииФНС": "<number>",
  "FSEndDate": "<date>",
  "FSIsClose": true,
  "fiscalItem": {
    "@ФискальныйНакопитель": "<number>",
    "Модель": "<text>",
    "НомерРегистрационный": "<text>",
    "Действующий": true,
    "ДатаОткрытия": null,
    "ДатаЗакрытия": null,
    "ККМ": "<number>"
  },
  "serviceSettings": {
    "ofdName": "<text>"
  },
  "license_info": {
    "activation_date": "<date>",
    "activation_end_date": "<date>",
    "deactivation_date": null,
    "end_license_date": "<date>",
    "license_type": "<text>",
    "is_trial": false
  },
  "MonitoringData": {
    "KKTRegId": "<text>",
    "FSClosed": false
  },
  "used_for": {
    "old_fn": "<text>"
  }
}
```

Значения `<number>`, `<text>` и `<date>` являются маркерами типов, а не
реальными данными проверенной кассы.

## Рекомендуемая функция извлечения

```python
import json


def _dict(value):
    return value if isinstance(value, dict) else {}


def _json_dict(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def extract_kkt_read_fields(detail: dict) -> dict:
    fiscal = _dict(detail.get("fiscalItem"))
    service = _dict(detail.get("serviceSettings"))
    license_info = _dict(detail.get("license_info"))
    monitoring = _dict(detail.get("MonitoringData"))
    used_for = _dict(detail.get("used_for"))
    if not used_for:
        used_for = _json_dict(detail.get("ГдеИспользуется"))

    active_value = detail.get("Действующая")
    active = active_value if isinstance(active_value, bool) else None

    return {
        "owner_inn": detail.get("ИНН"),
        "kkt_id": detail.get("@ККМ"),
        "kkt_reg_id": (
            detail.get("НомерРегистрационный")
            or monitoring.get("KKTRegId")
        ),
        "fn_number": (
            fiscal.get("НомерРегистрационный")
            or used_for.get("old_fn")
        ),
        "fn_model_raw": fiscal.get("Модель"),
        "fn_end_date": detail.get("FSEndDate"),
        "ofd_operator": service.get("ofdName"),
        "ofd_end_date": license_info.get("end_license_date"),
        "kkt_active": active,
        "fns_status_raw": detail.get("СтатусРегистрацииФНС"),
        "fn_closed": detail.get("FSIsClose"),
    }
```

`kkt_active=None` означает «неизвестно», а не «касса закрыта».

## Проверки перед использованием результата

1. `@ККМ` должен совпасть с `KKTId` запроса.
2. `НомерРегистрационный` должен совпасть с `KKTRegId` запроса.
3. `ИНН` должен совпасть с ИНН выбранного клиента.
4. Пустой `fiscalItem` допустим: 3 из 22 карточек не содержали полноценного ФН.
5. Пустой `license_info.end_license_date` допустим: использовать Registry
   fallback и сохранять источник значения.
6. `FSIsClose` не использовать для определения архивности кассы.
7. Не логировать целиком `headChief`, `Owner`, `Contract`, адреса и сырой ответ.

## Реализация в проекте

| Задача | Файл |
|---|---|
| Вызов `KKT.Read` | `services/kkt.py`, `get_kkt()` |
| Декодирование `d/s` | `decoder.py`, `SBISDecoder` |
| Номер и модель ФН | `parsers/kkt.py`, `extract_fn_number()` / `extract_fn_model()` |
| Срок ОФД | `parsers/kkt.py`, `extract_ofd_end_date()` |
| Общий обход | `services/live_collector.py`, `collect_kkt_by_inn()` |

Общая цепочка запросов описана в
[`sbis_billing_request_chain.md`](sbis_billing_request_chain.md), правила
формата RPC — в [`sbis_rpc_format.md`](sbis_rpc_format.md).
