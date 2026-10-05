import pytest
from conftest import fixture

from mrbilit_mcp.places import parse_chunk

pytestmark = pytest.mark.anyio


async def test_mb_find_place_flight(client, bundled):
    bundled["flight.atighgasht.com/api/Airports"] = fixture("airport_search.json")
    data = (await client.call_tool("mb_find_place", {"query": "استانبول", "mode": "flight"})).structured_content
    assert data["airports"] == []  # not an Iranian airport
    assert data["cities"] == [
        {
            "code": "ISTALL",
            "city": "Istanbul",
            "city_fa": "استانبول",
            "country": "Turkey",
            "airports": [{"code": "IST", "name": "Istanbul Airport"}, {"code": "SAW", "name": "Sabiha Gokcen Arpt"}],
        }
    ]
    data = (await client.call_tool("mb_find_place", {"query": "مشهد", "mode": "flight"})).structured_content
    assert data["airports"] == [{"code": "MHD", "city": "مشهد", "airport": "فرودگاه شهید هاشمی نژاد"}]
    paths = [r.url.path for r in bundled.calls]
    assert paths.count("/_nuxt/BcB8L5fh.js") == 1  # the bundled list is fetched once per process
    assert bundled.calls[-1].url.params["term"] == "مشهد"


async def test_mb_find_place_train_folds_arabic_letters(client, bundled):
    data = (await client.call_tool("mb_find_place", {"query": "كرمان", "mode": "train"})).structured_content
    assert data["stations"][0] == {"id": 167, "name": "کرمان", "slug": "kerman", "lat": 30.245409, "lon": 57.024017}
    data = (await client.call_tool("mb_find_place", {"query": "tehran", "mode": "train"})).structured_content
    assert data["stations"][0]["id"] == 1


async def test_bundled_list_falls_back_to_snapshot(client, api):
    # the home page and entry chunk are unreachable (404): the packaged snapshot answers
    data = (await client.call_tool("mb_find_place", {"query": "مشهد", "mode": "train"})).structured_content
    assert data["stations"][0]["id"] == 191
    assert [r.url.path for r in api.calls] == ["/"]


async def test_mb_find_place_bus_whole_city_first(client, api):
    api["bus.mrbilit.ir/api/CityList/GetBusCityList"] = fixture("bus_cities.json")
    data = (await client.call_tool("mb_find_place", {"query": "تهران", "mode": "bus"})).structured_content
    ids = [c["id"] for c in data["cities"]]
    assert ids[0] == 11320000 and set(ids[1:]) == {11321007, 11321006, 11321005, 11321004}
    assert data["cities"][0] == {
        "id": 11320000,
        "name": "تهران - همه پایانه\u200cها",
        "english": "Tehran - All Terminals",
        "slug": "tehran",
        "province": "استان تهران",
        "abroad": False,
    }


async def test_mb_find_place_isfahan_spellings(client, bundled):
    cities = fixture("bus_cities.json")
    esfahan = {"id": 21310000, "title": "اصفهان - همه پایانه\u200cها", "persianTitle": "اصفهان"}
    cities[0]["cities"].append({**esfahan, "englishTitle": "Esfahan - All Terminals", "code": "esfahan"})
    bundled["bus.mrbilit.ir/api/CityList/GetBusCityList"] = cities
    data = (await client.call_tool("mb_find_place", {"query": "isfahan", "mode": "bus"})).structured_content
    assert [c["id"] for c in data["cities"]] == [21310000]
    data = (await client.call_tool("mb_find_place", {"query": "esfahan", "mode": "train"})).structured_content
    assert data["stations"][0]["slug"] == "isfahan"


async def test_mb_find_place_flight_english_finds_domestic_airport(client, bundled):
    bundled["flight.atighgasht.com/api/Airports"] = [
        {"Code": "THRALL", "Title": "Tehran", "Airports": [{"Code": "THR"}, {"Code": "IKA"}]}
    ]
    data = (await client.call_tool("mb_find_place", {"query": "tehran", "mode": "flight"})).structured_content
    assert [a["code"] for a in data["airports"]] == ["THR"]  # the bundled list is Persian only


async def test_mb_find_place_taxi(client, api):
    api["bus.mrbilit.ir/api/CityList/GetTaxiCityList"] = fixture("taxi_cities.json")
    data = (await client.call_tool("mb_find_place", {"query": "Rasht", "mode": "taxi"})).structured_content
    assert [c["id"] for c in data["cities"]] == [54310000]


async def test_mb_find_place_hotel(client, api):
    api["hotel.mrbilit.ir/api/hotels/list"] = fixture("hotel_autocomplete.json")
    data = (
        await client.call_tool("mb_find_place", {"query": "mashhad", "mode": "hotel", "limit": 2})
    ).structured_content
    assert data["cities"] == [
        {"id": 16, "name": "مشهد", "slug": "mashhad", "tags": ["5star", "cheap", "hotelapartment", "luxury"]}
    ]
    assert data["hotels"][1] == {
        "id": 8778,
        "name": "انقلاب",
        "slug": "enghelab",
        "city": "مشهد",
        "city_slug": "mashhad",
    }
    assert len(data["hotels"]) == 2


async def test_mb_companies(client, api):
    api["flight.atighgasht.com/api/Airlines/GetAirlines"] = fixture("airlines.json")
    api["train.mrbilit.com/api/Corporations"] = fixture("train_companies.json")
    api["bus.mrbilit.ir/api/SuperCorporations"] = fixture("bus_companies.json")
    data = (await client.call_tool("mb_companies", {"mode": "flight", "query": "ir"})).structured_content
    assert data["companies"][0] == {"code": "IR", "name": "ایران ایر", "english": "Iran Air"}  # exact code first
    assert all(c["code"] for c in data["companies"])  # the empty placeholder row is dropped
    data = (await client.call_tool("mb_companies", {"mode": "train"})).structured_content
    assert {"ids": [25, 33, 35], "name": "فدک"} in data["companies"] and data["count"] == 13
    data = (await client.call_tool("mb_companies", {"mode": "bus", "query": "اکونومی"})).structured_content
    assert data["companies"] == [{"id": 278, "name": "اکونومی", "english": None}]


async def test_short_query_rejected(client, api):
    result = await client.call_tool("mb_find_place", {"query": "a", "mode": "bus"})
    assert result.is_error and not api.calls


def test_parse_chunk_numbers_and_booleans():
    rows = parse_chunk("const e=[{cities:[{id:2632e4,isDomestic:!0,isForeign:!1}],provinceID:0}];export{e as default};")
    assert rows == [{"cities": [{"id": 26320000, "isDomestic": True, "isForeign": False}], "provinceID": 0}]
