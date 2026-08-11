from pathlib import Path

from openpyxl import Workbook

from lookup import KKTInfo


def export_kkt(items: tuple[KKTInfo, ...], output_path: Path) -> int:
    """Формирует XLSX напрямую из результата в памяти."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ККТ"
    sheet.append(
        [
            "ИНН",
            "Владелец",
            "Модель",
            "РНМ",
            "Заводской номер",
            "Срок ФН",
            "Срок ОФД",
            "Адрес точки продаж",
        ]
    )
    for item in items:
        sheet.append(
            [
                item.owner_inn,
                item.owner_name,
                item.model,
                item.reg_number,
                item.manufacturer_number,
                item.fn_end_date,
                item.ofd_end_date,
                item.sales_point_address,
            ]
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    workbook.close()
    return len(items)
