from bs4 import BeautifulSoup

from monitor import compare_snapshots, parse_cards


def test_parse_cards_reads_advanced_format():
    html = """
    <html><body>
      <table>
        <tr>
          <th>Card Type</th>
          <th>Card Name</th>
          <th>Advanced Format</th>
          <th>Traditional Format</th>
          <th>Remarks</th>
        </tr>
        <tr>
          <td>Monster/Effect</td>
          <td>TEST CARD A</td>
          <td>Forbidden</td>
          <td>Limited</td>
          <td>New</td>
        </tr>
        <tr>
          <td>Spell</td>
          <td>TEST CARD B</td>
          <td>Limited</td>
          <td>Limited</td>
          <td></td>
        </tr>
        <tr>
          <td>Trap</td>
          <td>TEST CARD C</td>
          <td>Semi-Limited</td>
          <td>Semi-Limited</td>
          <td></td>
        </tr>
      </table>
    </body></html>
    """
    cards = parse_cards(BeautifulSoup(html, "html.parser"))

    assert cards == [
        {
            "name": "TEST CARD A",
            "status": "Forbidden",
            "type": "Monster/Effect",
            "remarks": "New",
        },
        {
            "name": "TEST CARD B",
            "status": "Limited",
            "type": "Spell",
            "remarks": "",
        },
        {
            "name": "TEST CARD C",
            "status": "Semi-Limited",
            "type": "Trap",
            "remarks": "",
        },
    ]


def test_compare_detects_moves_new_restrictions_and_unlimited():
    old = {
        "cards": [
            {"name": "A", "status": "Limited"},
            {"name": "B", "status": "Forbidden"},
            {"name": "C", "status": "Semi-Limited"},
        ]
    }
    new = {
        "cards": [
            {"name": "A", "status": "Forbidden"},
            {"name": "C", "status": "Semi-Limited"},
            {"name": "D", "status": "Limited"},
        ]
    }

    changes = compare_snapshots(old, new)

    assert changes == [
        {"name": "A", "from": "Limited", "to": "Forbidden"},
        {"name": "B", "from": "Forbidden", "to": "Unlimited"},
        {"name": "D", "from": "Unlimited", "to": "Limited"},
    ]
