import re
import requests
import time
from collections import defaultdict

from bs4 import BeautifulSoup
import pandas as pd


TRAVEL_TIME_CSV_FILE_PATH = "mrt_distance/travel_times.csv"


def write_travel_time_from_mrt_sg(station_name_to_code_mapping: dict, from_station_name: str, to_station_name: str):
    trip_duration = get_trip_duration(station_name_to_code_mapping, from_station_name, to_station_name)
    print(f"{from_station_name} to {to_station_name}: {trip_duration} minutes")

    with open(TRAVEL_TIME_CSV_FILE_PATH, "a") as travel_times_file:
        travel_times_file.write(f"\n{from_station_name},{to_station_name},{trip_duration}")


def get_trip_duration(station_name_to_code_mapping: dict, from_station_name: str, to_station_name: str):
    if from_station_name == to_station_name:
        return "0"

    # For politeness
    # time.sleep(1)

    from_station_code = station_name_to_code_mapping[from_station_name]
    to_station_code = station_name_to_code_mapping[to_station_name]

    url = "https://mrt.sg/tripcalc"
    form_data = {"station_a": from_station_code, "station_b": to_station_code}

    response = requests.post(url=url, data=form_data)
    raw_content = response.content

    soup = BeautifulSoup(raw_content, features="html.parser")
    string_soup = soup.prettify()

    trip_duration_regex = re.compile(r"This trip will take about (\d+) minutes")
    trip_duration = re.search(trip_duration_regex, string_soup).group(1)

    return trip_duration


def get_mrt_sg_station_code_mappings(exclude_lrts=False):
    with open("mrt_distance/mrt_sg_station_codes.txt") as raw_station_codes_file:
        raw_station_codes_list = raw_station_codes_file.readlines()
    
    if exclude_lrts:
        mrt_codes = ["NS", "EW", "CG", "NE", "CC", "CE", "DT", "TE"]
        raw_station_codes_list = [code for code in raw_station_codes_list if any([mrt_code in code for mrt_code in mrt_codes])]

    station_code_to_name_mapping = {}
    station_name_to_code_mapping = {}
    for row in raw_station_codes_list:
        station_metadata = row.split("|")
        station_code = station_metadata[0]
        station_name = station_metadata[1]
        station_code_to_name_mapping[station_code] = station_name
        station_name_to_code_mapping[station_name] = station_code
    
    return station_code_to_name_mapping, station_name_to_code_mapping


def scrape_station_travel_time(stations_to_measure, station_code_to_name_mapping, station_name_to_code_mapping):
    # Scraped so far: ["Raffles Place", "one-north", "Downtown", "Jurong East", "Expo"]
    #                 ["Paya Lebar", "Punggol", "Marina Bay", "Bugis", "Labrador Park"]
    #                 ["Bishan", "Orchard", "Tanjong Pagar", "Shenton Way", "Promenade", "Novena", "Tampines"]
    #                 ["Maxwell", "City Hall", "HarbourFront", "Holland Village", "Chinese Garden", "Woodlands", "Kranji", "Gardens by the Bay"]
    #                 ["Botanic Gardens", "Napier", "Beauty World", "Serangoon", "Upper Thomson", "Stadium", "Bayfront"]
    # NOT IN CURRENT DATASET: ["Tanjong Rhu", "Marine Parade"]

    currently_scraped = set(['Admiralty', 'Aljunied', 'Ang Mo Kio', 'Bartley', 'Bayfront', 'Beauty World', 'Bedok', 'Bedok North', 'Bedok Reservoir', 'Bencoolen', 'Bendemeer', 'Bishan', 'Boon Keng', 'Boon Lay', 'Botanic Gardens', 'Braddell', 'Bras Basah', 'Bright Hill', 'Buangkok', 'Bugis', 'Bukit Batok', 'Bukit Gombak', 'Bukit Panjang', 'Buona Vista', 'Caldecott', 'Canberra', 'Cashew', 'Changi Airport', 'Chinese Garden', 'City Hall', 'Downtown', 'Expo', 'Gardens by the Bay', 'HarbourFront', 'Holland Village', 'Jurong East', 'Kranji', 'Labrador Park', 'Marina Bay', 'Maxwell', 'Napier', 'Novena', 'Orchard', 'Paya Lebar', 'Promenade', 'Punggol', 'Raffles Place', 'Serangoon', 'Shenton Way', 'Stadium', 'Tampines', 'Tanjong Pagar', 'Upper Thomson', 'Woodlands', 'one-north'])

    current_index = 1
    total_stations_to_measure = len(stations_to_measure)

    for from_station_name in stations_to_measure:
        print(f"Scraping {current_index}/{total_stations_to_measure} stations...")
        list_of_station_names = [name for name in sorted(list(station_name_to_code_mapping.keys()))]
        for to_station_name in list_of_station_names:
            if to_station_name in currently_scraped:
                print(f"Passing on {to_station_name}")
                continue
            write_travel_time_from_mrt_sg(station_name_to_code_mapping, from_station_name, to_station_name)
        currently_scraped.add(from_station_name)
        current_index += 1


def get_residential_mrt_stations():
    return [
        "Admiralty",
        "Aljunied",
        "Ang Mo Kio",
        "Bartley",
        "Beauty World",
        "Bedok",
        "Bedok North",
        "Bedok Reservoir",
        "Bendemeer",
        "Bishan",
        "Boon Keng",
        "Boon Lay",
        "Botanic Gardens",
        "Braddell",
        "Bras Basah",
        "Bright Hill",
        "Buangkok",
        "Bukit Panjang",
        "Bugis",
        "Bukit Batok",
        "Bukit Gombak",
        "Buona Vista",
        "Caldecott",
        "Canberra",
        "Cashew",
        "Chinatown",
        "Chinese Garden",
        "Choa Chu Kang",
        "Clementi",
        "Commonwealth",
        "Dakota",
        "Dhoby Ghaut",
        "Dover",
        "Eunos",
        "Farrer Park",
        "Farrer Road",
        "Fort Canning",
        "Geylang Bahru",
        "Great World",
        "HarbourFront",
        "Havelock",
        "Haw Par Villa",
        "Hillview",
        "Holland Village",
        "Hougang",
        "Hume",
        "Jalan Besar",
        "Jurong East",
        "Kaki Bukit",
        "Kallang",
        "Kembangan",
        "Kent Ridge",
        "Khatib",
        "King Albert Park",
        "Kovan",
        "Labrador Park",
        "Lakeside",
        "Lavender",
        "Lentor",
        "Little India",
        "Lorong Chuan",
        "MacPherson",
        "Marina Bay",
        "Marsiling",
        "Marymount",
        "Mattar",
        "Maxwell",
        "Mayflower",
        "Mountbatten",
        "Napier",
        "Newton",
        "Nicoll Highway",
        "Novena",
        "Orchard",
        "Orchard Boulevard",
        "Outram Park",
        "Pasir Panjang",
        "Pasir Ris",
        "Paya Lebar",
        "Pioneer",
        "Potong Pasir",
        "Promenade",
        "Punggol",
        "Punggol Coast",
        "Queenstown",
        "Redhill",
        "Rochor",
        "Sembawang",
        "Sengkang",
        "Serangoon",
        "Shenton Way",
        "Simei",
        "Sixth Avenue",
        "Somerset",
        "Springleaf",
        "Stevens",
        "Tai Seng",
        "Tampines",
        "Tampines East",
        "Tampines West",
        "Tan Kah Kee",
        "Tanah Merah",
        "Tanjong Pagar",
        "Telok Blangah",
        "Tiong Bahru",
        "Toa Payoh",
        "Ubi",
        "Upper Changi",
        "Upper Thomson",
        "Woodlands",
        "Woodlands North",
        "Woodlands South",
        "Woodleigh",
        "Yew Tee",
        "Yio Chu Kang",
        "Yishun",
        "one-north",
    ]


def get_hdb_mrt_stations():
    return [
        "Admiralty",
        "Aljunied",
        "Ang Mo Kio",
        "Bartley",
        "Bayshore",
        "Beauty World",
        "Bedok",
        "Bedok North",
        "Bedok Reservoir",
        "Bendemeer",
        "Bishan",
        "Boon Keng",
        "Boon Lay",
        "Braddell",
        "Bright Hill",
        "Buangkok",
        "Bukit Panjang",
        "Bukit Batok",
        "Bukit Gombak",
        "Buona Vista",
        "Caldecott",
        "Canberra",
        "Chinese Garden",
        "Choa Chu Kang",
        "Clementi",
        "Commonwealth",
        "Dakota",
        "Dover",
        "Eunos",
        "Farrer Park",
        "Farrer Road",
        "Geylang Bahru",
        "Havelock",
        "Holland Village",
        "Hougang",
        "Jalan Besar",
        "Jurong East",
        "Kaki Bukit",
        "Kallang",
        "Khatib",
        "Kovan",
        "Labrador Park",
        "Lakeside",
        "Lavender",
        "Lentor",
        "Little India",
        "MacPherson",
        "Marine Terrace",
        "Marsiling",
        "Marymount",
        "Mattar",
        "Mayflower",
        "Mountbatten",
        # "Outram Park",
        "Pasir Ris",
        "Paya Lebar",
        "Pioneer",
        "Potong Pasir",
        "Punggol",
        "Punggol Coast",
        "Queenstown",
        "Redhill",
        "Rochor",
        "Sembawang",
        "Sengkang",
        "Serangoon",
        "Simei",
        "Tai Seng",
        "Tampines",
        "Tampines East",
        "Tampines West",
        "Tanah Merah",
        "Tanjong Rhu",
        "Telok Blangah",
        "Tiong Bahru",
        "Toa Payoh",
        "Ubi",
        "Upper Thomson",
        "Woodlands",
        "Woodlands North",
        "Woodlands South",
        "Woodleigh",
        "Yew Tee",
        "Yio Chu Kang",
        "Yishun",
    ]


def get_weights():
    possible_general_raw_percentage_weights = {

        # Worth 40%
        "work_central_business_district": {
            "Raffles Place": 10.0,
            "Marina Bay": 5.0,
            "Downtown": 5.0,
            "Tanjong Pagar": 5.0,
            "Shenton Way": 5.0,
            "Bugis": 5.0,
            "Orchard": 5.0,
        },
        
        # Worth 20%
        "work_decentralized_nodes": {
            "one-north": 2.5,
            "Jurong East": 2.5,
            "Punggol Coast": 2.5,
            "Expo": 2.5,
            "Paya Lebar": 2.5,
            "Labrador Park": 2.5,
            "Tampines": 2.5,
            "Woodlands": 2.5,
        },

        # Worth 25%
        "common_commercial_areas": {
            # Central
            "Orchard": 1.5,
            "Somerset": 1.5,
            "Dhoby Ghaut": 1.5,
            "Promenade": 1.5,
            "City Hall": 1.5,
            "Bugis": 1.5,
            "Maxwell": 1.5,
            "Bayfront": 1.5,

            # Decentralized
            "HarbourFront": 1.5,
            "Holland Village": 1.5,
            "Marine Parade": 1.5,
            "Tampines": 1.5,
            "Punggol": 1.5,
            "Woodlands": 1.5,
            "Paya Lebar": 1.5,
            "Serangoon": 1.5,
            "Stadium": 2.5,
        },

        # Worth 12.5%
        "common_nature_areas": {
            "Gardens by the Bay": 2.5,
            "Botanic Gardens": 1.25,  # Both this and Napier are acceptable entrances
            "Napier": 1.25,
            "Chinese Garden": 2.5,
            "Upper Thomson": 1.5,  # Central Water Catchment
            "Tanjong Rhu": 2.5,  # best entry to East Coast Park
        },

        # Worth 2.5%
        "miscellaneous_areas": {
            "Kranji": 1.0,  # JB - In the future, Woodlands North will be the exit point
            "Changi Airport": 1.0,
        }
    }

    # Mine
    raw_percentage_weights = {

        # Worth 40%
        "work_central_business_district": {
            "Maxwell": 50.0,
            # "Raffles Place": 10.0,
            # "Marina Bay": 2.5,
            # "Downtown": 2.5,
            # "Tanjong Pagar": 2.5,
            # "Shenton Way": 2.5,
            # "Bugis": 2.5,
            # "one-north": 10.0,
            # "Jurong East": 2.5,
            # "Paya Lebar": 2.5,
            # "Labrador Park": 2.5,
        },

        # Worth 25%
        "common_commercial_areas": {
            "Orchard": 3.0,
            "Somerset": 3.0,
            "Dhoby Ghaut": 3.0,
            "Promenade": 5.0,
            "City Hall": 4.0,
            "Bugis": 5.0,
            "Bayfront": 4.0,
            "HarbourFront": 5.0,
            "Holland Village": 5.0,
            "Stadium": 4.0,

            # "Orchard": 2.5,
            # "Somerset": 2.5,
            # "Dhoby Ghaut": 2.5,
            # "Promenade": 2.5,
            # "City Hall": 2.5,
            # "Bugis": 2.5,
            # "Bayfront": 2.5,
            # "HarbourFront": 2.5,
            # "Holland Village": 2.5,
            # "Serangoon": 2.5,
            # "Stadium": 2.5,
        },

        # Worth 12.5%
        "common_nature_areas": {
            "Gardens by the Bay": 2.0,
            "Botanic Gardens": 1.0,  # Both this and Napier are acceptable entrances
            "Napier": 1.0,
            "Chinese Garden": 2.0,
            "Tanjong Rhu": 2.0,  # best entry to East Coast Park
            # "Gardens by the Bay": 2.5,
            # "Botanic Gardens": 1.25,  # Both this and Napier are acceptable entrances
            # "Napier": 1.25,
            # "Chinese Garden": 2.5,
            # "Tanjong Rhu": 2.5,  # best entry to East Coast Park
        },

        # Worth 2.5%
        "miscellaneous_areas": {
            "Woodlands North": 1.0,  # JB
        }
    }

    percentage_weights = defaultdict(float)
    for category_name, category_weights in raw_percentage_weights.items():
        for station_name, weight in category_weights.items():
            percentage_weights[station_name] += weight

    total_weight = sum([weight for station_name, weight in percentage_weights.items()])
    assert total_weight == 100, f"{total_weight=} should sum to 100, as a percentage."
    return percentage_weights


def read_travel_time_data():
    travel_times = pd.read_csv(TRAVEL_TIME_CSV_FILE_PATH, header=0)
    return travel_times


COLUMN_FROM_STATION_NAME = "from_station_name"
COLUMN_TO_STATION_NAME = "to_station_name"
COLUMN_WEIGHT = "weight"
COLUMN_TRIP_DURATION_IN_MINUTES = "trip_duration_in_minutes"
COLUMN_WEIGHTED_TRIP_DURATION = "weighted_trip_duration"


def calculate_ratings():
    travel_times = read_travel_time_data()
    weights = get_weights()
    residential_estates = get_hdb_mrt_stations()
    # residential_estates = get_residential_mrt_stations()

    mrt_stations_with_weights = list(weights.keys())
    # Only analyze trip durations that start from the stations you specified weights for
    travel_times = travel_times[travel_times[COLUMN_FROM_STATION_NAME].isin(mrt_stations_with_weights)]
    # Only keep trip durations that end from possible residential estates
    travel_times = travel_times[travel_times[COLUMN_TO_STATION_NAME].isin(residential_estates)]
    # Convert percentage to decimal; identify weights for each station
    travel_times[COLUMN_WEIGHT] = travel_times[COLUMN_FROM_STATION_NAME].map(weights).map(lambda x: x/100)
    # For now, simply calculated weighted average
    travel_times[COLUMN_WEIGHTED_TRIP_DURATION] = travel_times[COLUMN_WEIGHT] * travel_times[COLUMN_TRIP_DURATION_IN_MINUTES]

    travel_times = travel_times[[COLUMN_TO_STATION_NAME, COLUMN_WEIGHTED_TRIP_DURATION]]
    travel_times = travel_times.groupby([COLUMN_TO_STATION_NAME]).sum()
    travel_times = travel_times.sort_values(by=COLUMN_WEIGHTED_TRIP_DURATION, ascending=True)

    print(travel_times.to_string())


def add_new_leaf_node_data(existing_leaf_node_station_name, new_leaf_node_station_name, travel_duration, waiting_duration):
    travel_times = read_travel_time_data()
    new_node_travel_times = travel_times[travel_times[COLUMN_FROM_STATION_NAME] == existing_leaf_node_station_name]
    new_node_travel_times.loc[:, COLUMN_FROM_STATION_NAME] = new_leaf_node_station_name
    # Calculate trip distances by adding the duration between old and new leaf nodes
    new_node_travel_times.loc[:, COLUMN_TRIP_DURATION_IN_MINUTES] += travel_duration
    # Old leaf to new leaf needs to include waiting time
    new_node_travel_times.loc[new_node_travel_times[COLUMN_TO_STATION_NAME] == existing_leaf_node_station_name, COLUMN_TRIP_DURATION_IN_MINUTES] = travel_duration + waiting_duration
    # New leaf has 0 travel time to itself
    new_node_travel_times.loc[-1] = [new_leaf_node_station_name, new_leaf_node_station_name, 0]

    # Reflexive behavior: duration from X to Y == duration from Y to X
    swapped_df = new_node_travel_times.loc[:, [COLUMN_TO_STATION_NAME, COLUMN_FROM_STATION_NAME, COLUMN_TRIP_DURATION_IN_MINUTES]]
    swapped_df = swapped_df.rename(axis="columns", mapper={COLUMN_FROM_STATION_NAME: COLUMN_TO_STATION_NAME, COLUMN_TO_STATION_NAME: COLUMN_FROM_STATION_NAME, COLUMN_TRIP_DURATION_IN_MINUTES: COLUMN_TRIP_DURATION_IN_MINUTES})

    print(new_node_travel_times.shape)
    print(swapped_df.shape)
    travel_times_combined = pd.concat([travel_times, new_node_travel_times, swapped_df], axis=0)
    print(travel_times_combined.shape)
    travel_times_combined = travel_times_combined.drop_duplicates()
    travel_times_combined = travel_times_combined.sort_values(by=[COLUMN_FROM_STATION_NAME, COLUMN_TO_STATION_NAME])
    print(travel_times_combined.shape)
    travel_times_combined.to_csv(TRAVEL_TIME_CSV_FILE_PATH, index=False)



if __name__ == "__main__":
    calculate_ratings()

    # TODO: Get datamall average transit tap-in-tap-out pairs for Jan-Jun 2025
    # TODO: Normalize for Hume (which doesn't span the whole timeframe)
    # TODO: Create estimate for where people live by taking the average weekday morning tap-in (7-10am)
    # TODO: Subtract some weighted average (must sanity-check) of where people live from the original data, to get the commercial areas data

    # TODO: Get some statistics for biggest peak crowd, and overall daily activity of locations
    # TODO: Calculate 'crowdedness rating' based on these factors

    # TODO: Allow linear and quadratic loss calculation (or just provide a lambda)
    # TODO: Calculate loss based on island-wide standard deviations (e.g. 2 standard deviations away means +2 score) for each target node
    # TODO: Calculate straight-line distance

    # TODO: Make final conclusion based on some weighted average of MRT trip duration, straight-line distance, and 'crowdedness rating'.



# Other code


    # WAITING_TIME_IN_MINUTES = 4
    # NEW_LEAF_NODES_TO_ADD = [
    #     ("Punggol", "Punggol Coast", 2, WAITING_TIME_IN_MINUTES),
    #     ("Gardens by the Bay", "Tanjong Rhu", 4, WAITING_TIME_IN_MINUTES),
    #     ("Tanjong Rhu", "Katong Park", 2, WAITING_TIME_IN_MINUTES),
    #     ("Katong Park", "Tanjong Katong", 2, WAITING_TIME_IN_MINUTES),
    #     ("Tanjong Katong", "Marine Parade", 1, WAITING_TIME_IN_MINUTES),
    #     ("Marine Parade", "Marine Terrace", 2, WAITING_TIME_IN_MINUTES),
    #     ("Marine Terrace", "Siglap", 2, WAITING_TIME_IN_MINUTES),
    #     ("Siglap", "Bayshore", 2, WAITING_TIME_IN_MINUTES),

    #     Complicated move: To create Hume, first create leaves from both sides, then take the minimum via Google Gemini (I shit you not)
    #     ("Beauty World", "Hume", 2, WAITING_TIME_IN_MINUTES),
    #     ("Hillview", "Hume", 1, WAITING_TIME_IN_MINUTES),
    # ]
    # for new_leaf_node_data in NEW_LEAF_NODES_TO_ADD:
    #     add_new_leaf_node_data(*new_leaf_node_data)

    # travel_times = read_travel_time_data()
    # duplicate_mask = travel_times.duplicated()
    # duplicate_rows = travel_times[duplicate_mask]
    # print("Duplicate rows (keeping the first occurrence as non-duplicate):")
    # print(duplicate_rows)

    # with open(TRAVEL_TIME_CSV_FILE_PATH) as current:
    #     with open("mrt_distance/data/backup_of_travel_times_20250702.csv") as old:
    #         current_lines = current.readlines()
    #         hash_current = {f"{line.split(',')[0]},{line.split(',')[1]}": line.split(',')[2] for line in current_lines}
    #         old_lines = old.readlines()
    #         for line in old_lines:
    #             values = line.split(',')
    #             key = f"{values[0]},{values[1]}"
    #             value = values[2]
    #             if key not in hash_current:
    #                 print(f"{key=} not found")
    #             else:
    #                 if hash_current[key] != value:
    #                     print(f"{value=} mismatch with {hash_current[key]=}")
    #                     print(f"{line=} {key=}")


    # print(travel_times.shape)
    # condition_to_keep = travel_times[COLUMN_TO_STATION_NAME] != 'Hume'
    # travel_times = travel_times[condition_to_keep]
    # print(travel_times.shape)
    # swapped_df = travel_times.loc[:, [COLUMN_TO_STATION_NAME, COLUMN_FROM_STATION_NAME, COLUMN_TRIP_DURATION_IN_MINUTES]]
    # swapped_df = swapped_df.rename(axis="columns", mapper={COLUMN_FROM_STATION_NAME: COLUMN_TO_STATION_NAME, COLUMN_TO_STATION_NAME: COLUMN_FROM_STATION_NAME, COLUMN_TRIP_DURATION_IN_MINUTES: COLUMN_TRIP_DURATION_IN_MINUTES})

    # print(travel_times.shape)
    # print(swapped_df.shape)
    # travel_times_combined = pd.concat([travel_times, swapped_df], axis=0)
    # print(travel_times_combined.shape)
    # travel_times_combined = travel_times_combined.drop_duplicates()
    # travel_times_combined = travel_times_combined.sort_values(by=[COLUMN_FROM_STATION_NAME, COLUMN_TO_STATION_NAME])
    # print(travel_times_combined.shape)
    # travel_times_combined.to_csv(TRAVEL_TIME_CSV_FILE_PATH, index=False)

    # travel_times = read_travel_time_data()
    # to_add = ['Bukit Panjang', 'Cashew', 'Hillview']
    # def to_apply(row):
    #     if (row[COLUMN_FROM_STATION_NAME] in to_add and row[COLUMN_TO_STATION_NAME] not in to_add) or (row[COLUMN_TO_STATION_NAME] in to_add and row[COLUMN_FROM_STATION_NAME] not in to_add):
    #         row[COLUMN_TRIP_DURATION_IN_MINUTES] += 1
    #     return row
    # travel_times = travel_times.apply(to_apply, axis=1)
    # travel_times.to_csv("mrt_distance/travel_times_final.csv", index=False)

    # from_stations = travel_times[COLUMN_FROM_STATION_NAME]
    # to_stations = travel_times[COLUMN_TO_STATION_NAME]
    
    # from_stations = from_stations.drop_duplicates()
    # to_stations = to_stations.drop_duplicates()
    
    # from_stations = set(from_stations.to_list())

    # # print(sorted(list(from_stations)))

    # to_stations = set(to_stations.to_list())

    # missing_in_from = to_stations - from_stations

    # STATION_NAMES_TO_SCRAPE = sorted(list(missing_in_from))
    
    # station_code_to_name_mapping, station_name_to_code_mapping = get_mrt_sg_station_code_mappings(exclude_lrts=True)
    # scrape_station_travel_time(STATION_NAMES_TO_SCRAPE, station_code_to_name_mapping, station_name_to_code_mapping)
