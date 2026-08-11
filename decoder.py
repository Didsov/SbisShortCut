import json


class SBISDecoder:

    def decode(self, obj):

        if isinstance(obj, list):
            return [self.decode(x) for x in obj]

        if not isinstance(obj, dict):
            return obj

        obj_type = obj.get("_type")

        if obj_type == "record":
            return self._decode_record(obj)

        if obj_type == "recordset":
            return self._decode_recordset(obj)

        return {
            key: self.decode(value)
            for key, value in obj.items()
        }

    def _decode_record(self, record):

        schema = record.get("s", [])
        values = record.get("d", [])

        result = {}

        for field, value in zip(schema, values):

            name = field["n"]

            result[name] = self.decode(value)

        return result

    def _decode_recordset(self, recordset):

        schema = recordset.get("s", [])
        rows = recordset.get("d", [])

        result = []

        for row in rows:

            # если строка уже запись
            if isinstance(row, dict):
                result.append(self.decode(row))
                continue

            # обычный список значений
            if isinstance(row, list):

                item = {}

                for field, value in zip(schema, row):
                    item[field["n"]] = self.decode(value)

                result.append(item)

                continue

            result.append(self.decode(row))

        return result


if __name__ == "__main__":

    with open("response.json", encoding="utf-8") as f:
        raw = json.load(f)

    decoder = SBISDecoder()

    decoded = decoder.decode(raw)

    with open(
        "decoded.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            decoded,
            f,
            ensure_ascii=False,
            indent=4
        )

    print("Готово.")