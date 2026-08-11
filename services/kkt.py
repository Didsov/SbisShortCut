from sbis_client import SBISClient
from decoder import SBISDecoder


def get_kkt(
    account_id,
    kkt_id,
    kkt_reg_id,
    client: SBISClient | None = None,
):
    client = client or SBISClient()

    decoder = SBISDecoder()
    response = client.call(
        "KKT.Read",
        {
            "Params": {
                "d": [
                    account_id,
                    kkt_id,
                    kkt_reg_id
                ],
                "s": [
                    {
                        "t": "Число целое",
                        "n": "AccountId"
                    },
                    {
                        "t": "Число целое",
                        "n": "KKTId"
                    },
                    {
                        "t": "Строка",
                        "n": "KKTRegId"
                    }
                ],
                "_type": "record",
                "f": 0
            }
        }
    )
    if "error" in response:
        raise RuntimeError(
            response["error"].get("message", "KKT.Read error")
        )

    return decoder.decode(response["result"])
