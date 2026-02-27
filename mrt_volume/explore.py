from dataclasses import dataclass
import os
from matplotlib.axes import Axes
import requests

import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt


URL__PASSENGER_VOLUME_BY_ORIGIN_DESTINATION_TRAIN_STATIONS = "https://datamall2.mytransport.sg/ltaodataservice/PV/ODTrain"
URL__PASSENGER_VOLUME_BY_TRAIN_STATIONS = "https://datamall2.mytransport.sg/ltaodataservice/PV/Train"

URL__STATION_CODES_AND_NAMES = ""


def request_for_data():
    """
    The following API Account Key grants you access to all dynamic / real-time datasets in DataMall. 
    For instructions on how to access the APIs and make use of the datasets, you may refer to the API User Guide here. 
    [https://datamall.lta.gov.sg/content/dam/datamall/datasets/LTA_DataMall_API_User_Guide.pdf]
    """
    date = "202504"  # Note: Only last 3 full months of data are available
    url = f"{URL__PASSENGER_VOLUME_BY_ORIGIN_DESTINATION_TRAIN_STATIONS}?Date={date}"
    response = requests.get(url=url, headers={"AccountKey": "RomoaQ6ATPuLJ2PyRmXi2g=="})
    print(response.content)


MONTH_UNDER_ANALYSIS = "202506"
DATA_PATH__PASSENGER_VOLUME_BY_TRAIN_STATIONS = os.path.join(os.getcwd(), "mrt_volume", "data", "station_volumes", f"transport_node_train_{MONTH_UNDER_ANALYSIS}.csv")
DATA_PATH__TRAIN_STATION_CODES_AND_NAMES = os.path.join(os.getcwd(), "mrt_volume", "data", "train_station_codes_and_names", "train_station_codes_and_names.xls")


# Data column names from passenger volume dataset
STATION_CODE = "PT_CODE"
TIME_OF_DAY = "TIME_PER_HOUR"
TOTAL_TAP_IN_VOLUME = "TOTAL_TAP_IN_VOLUME"
TOTAL_TAP_OUT_VOLUME = "TOTAL_TAP_OUT_VOLUME"
DAY_TYPE = "DAY_TYPE"
WEEKDAY = "WEEKDAY"
NOT_WEEKDAY = "WEEKENDS/HOLIDAY"

# Data column names from mapping dataset
MAPPING_STATION_CODE = "stn_code"
ENGLISH_STATION_NAME = "mrt_station_english"
MRT_LINE = "mrt_line_english"

# Own analysis variable names
DAILY_AVERAGE_TAP_IN_VOLUME = "DAILY_AVERAGE_TAP_IN_VOLUME"
DAILY_AVERAGE_TAP_OUT_VOLUME = "DAILY_AVERAGE_TAP_OUT_VOLUME"
DAILY_AVERAGE_TOTAL_VOLUME = "DAILY_AVERAGE_TOTAL_VOLUME"
DAILY_AVERAGE_VOLUME_PERCENT_DELTA = "DAILY_AVERAGE_VOLUME_PERCENT_DELTA"
STATION_NAME = "STATION_NAME"

ALL_DAY = "ALL DAY"
WEEKDAY_MINUS_WEEKEND = "WEEKDAY MINUS WEEKEND"
WEEKEND_MINUS_WEEKDAY = "WEEKEND MINUS WEEKDAY"


# Analysis function return type
@dataclass
class AnalysisGraphParams:
    axes: Axes
    title: str
    x_label: str = "Stations"
    y_label: str = "Average Daily Passenger Volume"


def get_station_code_to_name_mapping() -> pd.DataFrame:
    mapping_df = pd.read_excel(io=DATA_PATH__TRAIN_STATION_CODES_AND_NAMES)
    mapping_df = mapping_df[[MAPPING_STATION_CODE, ENGLISH_STATION_NAME, MRT_LINE]]
    mapping_df = mapping_df.rename({MAPPING_STATION_CODE: STATION_CODE, ENGLISH_STATION_NAME: STATION_NAME}, axis="columns")
    return mapping_df


def normalize_for_day_type(dataframe: pd.DataFrame, day_type: str) -> pd.DataFrame:
    _number_of_weekdays_in_aug_2024 = 21
    _number_of_non_weekdays_in_aug_2024 = 10
    _total_days_in_aug_2024 = _number_of_weekdays_in_aug_2024 + _number_of_non_weekdays_in_aug_2024
    
    # Get weighted average daily amount
    for totals_column, daily_average_column in (
        (TOTAL_TAP_IN_VOLUME, DAILY_AVERAGE_TAP_IN_VOLUME), 
        (TOTAL_TAP_OUT_VOLUME, DAILY_AVERAGE_TAP_OUT_VOLUME)
        ):
        if day_type == WEEKDAY:
            dataframe[daily_average_column] = dataframe[totals_column] / _number_of_weekdays_in_aug_2024
            dataframe = dataframe[dataframe[DAY_TYPE] == day_type]
        elif day_type == NOT_WEEKDAY:
            dataframe[daily_average_column] = dataframe[totals_column] / _number_of_non_weekdays_in_aug_2024
            dataframe = dataframe[dataframe[DAY_TYPE] == day_type]
        elif day_type == ALL_DAY:
            # We want to find the average day - so since the data is in totals, the normalizing factor is just the number of days
            dataframe[daily_average_column] = dataframe[totals_column] / _total_days_in_aug_2024
        else:
            # For delta calculations, we need to individually normalize weekdays and non-weekdays
            dataframe[daily_average_column] = np.where(dataframe[DAY_TYPE] == WEEKDAY,
                                                       dataframe[totals_column] / _number_of_weekdays_in_aug_2024,
                                                       dataframe[totals_column] / _number_of_non_weekdays_in_aug_2024)
    return dataframe


def find_overall_station_busyness(dataframe: pd.DataFrame, day_type: str) -> AnalysisGraphParams:
    """Find the busiest and least busy stations across the day"""
    assert day_type in (WEEKDAY, NOT_WEEKDAY, ALL_DAY), f"Busyness calculations cannot handle {day_type=}"

    BUSIEST = 10
    LEAST_BUSY = 10
    dataframe = dataframe.groupby(by=STATION_NAME).agg("sum")
    dataframe = dataframe.sort_values(by=DAILY_AVERAGE_TOTAL_VOLUME, ascending=False)
    dataframe = pd.concat([dataframe.head(BUSIEST), dataframe.tail(LEAST_BUSY)], axis=0)
    ax = sns.barplot(data=dataframe, x=dataframe.index, y=dataframe[DAILY_AVERAGE_TOTAL_VOLUME])
    return AnalysisGraphParams(
        axes=ax,
        title=f"The {BUSIEST} busiest and {LEAST_BUSY} least busy stations on {day_type}s for YYYYMM {MONTH_UNDER_ANALYSIS}"
    )


def find_biggest_delta_stations(dataframe: pd.DataFrame, day_type: str) -> AnalysisGraphParams:
    """Find stations with the biggest delta between day_type and the converse."""
    def get_sorted_df(df, day_type: str) -> pd.DataFrame:
        df = dataframe[dataframe[DAY_TYPE] == day_type]
        df.sort_values([STATION_NAME, TIME_OF_DAY])
        df = df[[DAILY_AVERAGE_TOTAL_VOLUME, STATION_NAME]]
        df = df.groupby(by=STATION_NAME).agg("sum")
        return df

    assert day_type in (WEEKDAY_MINUS_WEEKEND, WEEKEND_MINUS_WEEKDAY), f"Delta calculations cannot handle {day_type=}"

    # We need to get the sorted versions of WEEKDAY and NOT_WEEKDAY so that we can do elementwise comparisons
    weekday_df = get_sorted_df(dataframe, WEEKDAY)
    non_weekday_df = get_sorted_df(dataframe, NOT_WEEKDAY)

    # Get the percentage difference, in an elementwise fashion
    if day_type == WEEKDAY_MINUS_WEEKEND:
        delta_df = weekday_df - non_weekday_df
        delta_df[DAILY_AVERAGE_VOLUME_PERCENT_DELTA] = delta_df[DAILY_AVERAGE_TOTAL_VOLUME] / weekday_df[DAILY_AVERAGE_TOTAL_VOLUME]
    else:
        delta_df = non_weekday_df - weekday_df
        delta_df[DAILY_AVERAGE_VOLUME_PERCENT_DELTA] = delta_df[DAILY_AVERAGE_TOTAL_VOLUME] / non_weekday_df[DAILY_AVERAGE_TOTAL_VOLUME]
    delta_df[DAILY_AVERAGE_VOLUME_PERCENT_DELTA] = delta_df[DAILY_AVERAGE_VOLUME_PERCENT_DELTA] * 100

    # For ease of calculation of percentages, remove everything less than 0 percent
    delta_df = delta_df[delta_df[DAILY_AVERAGE_VOLUME_PERCENT_DELTA] >= 0]

    MOST_DIFF = 30
    LEAST_DIFF = 0

    delta_df = delta_df.sort_values(by=DAILY_AVERAGE_VOLUME_PERCENT_DELTA, ascending=False)
    delta_df = pd.concat([delta_df.head(MOST_DIFF), delta_df.tail(LEAST_DIFF)], axis=0)

    ax = sns.barplot(data=delta_df, x=delta_df.index, y=delta_df[DAILY_AVERAGE_VOLUME_PERCENT_DELTA])
    return AnalysisGraphParams(
        axes=ax,
        title=f"The largest {MOST_DIFF} stations and smallest {LEAST_DIFF} stations with positive percentage delta ({day_type}) for YYYYMM {MONTH_UNDER_ANALYSIS}",
        y_label=f"Percentage difference ({day_type})"
    )


def compare_station_busyness_by_hour(dataframe: pd.DataFrame, day_type: str) -> AnalysisGraphParams:
    """Compare some choice of several stations' daily traffic"""
    assert day_type in (WEEKDAY, NOT_WEEKDAY, ALL_DAY), f"Hourly calculations cannot handle {day_type=}"

    STATIONS = ("Tampines", "Tampines East", "Tampines West")
    dataframe = dataframe[dataframe[STATION_NAME].isin(STATIONS)]
    ax = sns.lineplot(data=dataframe, x=dataframe[TIME_OF_DAY], y=dataframe[DAILY_AVERAGE_TOTAL_VOLUME], hue=dataframe[STATION_NAME], errorbar=None)
    return AnalysisGraphParams(
        axes=ax,
        title=f"Comparison of passenger in/out volume at stations {STATIONS} on {day_type}s for YYYYMM {MONTH_UNDER_ANALYSIS}",
        x_label="Hour of the day (0 to 24)"
    )


def show_plot_from_data(analysis_function, day_type, include_lrt_data, file_path):
    df = pd.read_csv(filepath_or_buffer=file_path)

    # Intersections in this dataset are stored as NS1/EW2/NE3 - but in the name dataset, they are split.
    #       We therefore split it up to allow for easier merging later.
    df[STATION_CODE] = df[STATION_CODE].str.split("/")
    df = df.explode(column=STATION_CODE, ignore_index=True)

    # Enrich data with station names
    mapping_df = get_station_code_to_name_mapping()
    df = df.merge(mapping_df, on=STATION_CODE)

    # Data given is total data, so normalization for individual days needs to be done
    df = normalize_for_day_type(dataframe=df, day_type=day_type)

    # Exclude LRTs since they will generally have outlier traffic (optional)
    if not include_lrt_data:
        df = df[~df[MRT_LINE].str.contains("LRT")]

    # Generate internal analysis columns
    df[DAILY_AVERAGE_TOTAL_VOLUME] = df[DAILY_AVERAGE_TAP_IN_VOLUME] + df[DAILY_AVERAGE_TAP_OUT_VOLUME]
    df[STATION_NAME] = df[STATION_NAME].str.strip()
    df = df[[TIME_OF_DAY, DAILY_AVERAGE_TOTAL_VOLUME, STATION_NAME, DAILY_AVERAGE_TAP_IN_VOLUME, DAILY_AVERAGE_TAP_OUT_VOLUME, DAY_TYPE]]
    df = df.drop_duplicates(ignore_index=True)

    # Run analysis
    graph_params = analysis_function(dataframe=df, day_type=day_type)

    # General labelling
    axes = graph_params.axes
    axes.set_title(graph_params.title)
    axes.set_xlabel(graph_params.x_label)
    axes.set_ylabel(graph_params.y_label)
    plt.xticks(rotation=30)

    # Show plot
    plt.show()


if __name__ == "__main__":
    # request_for_data()

    show_plot_from_data(

        # analysis_function is one of: find_overall_station_busyness, compare_station_busyness_by_hour, find_biggest_delta_stations
        analysis_function=compare_station_busyness_by_hour, 
        
        # day_type is one of: WEEKDAY, NOT_WEEKDAY, ALL_DAY, WEEKDAY_MINUS_WEEKEND, WEEKEND_MINUS_WEEKDAY
        day_type=WEEKDAY, 
        # day_type=WEEKDAY_MINUS_WEEKEND, 

        include_lrt_data=False, 
        file_path=DATA_PATH__PASSENGER_VOLUME_BY_TRAIN_STATIONS
    )


"""
TODO: Explore visualizations of all the stations in the dataset
TODO: Explore the other dataset
TODO:
Other possibilities:
- Estimate flow of passengers through stations based on a cache table of optimal routes? e.g. Yew Tee to Raffles Place passes through Jurong Eat
- How many people will pass through Havelock going downtown? This would require analysis of in-out pairs where the optimal route passes through Havelock in the morning.
- Estimate the percentage of people taking the train? Based on resident population estimates and their nearest locations
- Where does everyone go to on Sunday mornings? (e.g. 7am to 9am tap-ins, and their tap-outs) wat are the chirp chirps doing because the hoot hoot dun understand ?-?

- Find some GIS software and overlay a map of Singapore with the HDB population/other population, and calculate distance from train stations?

"""