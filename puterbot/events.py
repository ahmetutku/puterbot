import time

from loguru import logger
from pprint import pformat
from scipy.spatial import distance
import numpy as np

from puterbot.common import KEY_EVENTS, MOUSE_EVENTS
from puterbot.crud import (
    get_input_events,
    get_window_events,
    get_screenshots,
)
from puterbot.models import InputEvent
from puterbot.utils import (
    get_double_click_distance_pixels,
    get_double_click_interval_seconds,
    get_scale_ratios,
    rows2dicts,
)


MAX_PROCESS_ITERS = 1


def get_events(recording, process=True, meta=None):
    start_time = time.time()
    input_events = get_input_events(recording)
    window_events = get_window_events(recording)
    screenshots = get_screenshots(recording)
    #print(f"\n\n\n\ndwagdwag{len(screenshots)}\n\n\n\n")

    raw_input_event_dicts = rows2dicts(input_events)
    logger.debug(f"raw_input_event_dicts=\n{pformat(raw_input_event_dicts)}")

    num_input_events = len(input_events)
    num_window_events = len(window_events)
    num_screenshots = len(screenshots)

    num_input_events_raw = num_input_events
    num_window_events_raw = num_window_events
    num_screenshots_raw = num_screenshots
    duration_raw = input_events[-1].timestamp - input_events[0].timestamp

    num_process_iters = 0
    if process:
        while True:
            logger.info(
                f"{num_process_iters=} "
                f"{num_input_events=} "
                f"{num_window_events=} "
                f"{num_screenshots=}"
            )
            input_events, window_events, screenshots = process_events(
                input_events, window_events, screenshots,
            )
            if (
                len(input_events) == num_input_events and
                len(window_events) == num_window_events and
                len(screenshots) == num_screenshots
            ):
                break
            num_process_iters += 1
            num_input_events = len(input_events)
            num_window_events = len(window_events)
            num_screenshots = len(screenshots)
            if num_process_iters == MAX_PROCESS_ITERS:
                break

    if meta is not None:
        format_num = (
            lambda num, raw_num: f"{num} of {raw_num} ({(num / raw_num):.2%})"
        )
        meta["num_process_iters"] = num_process_iters
        meta["num_input_events"] = format_num(
            num_input_events, num_input_events_raw,
        )
        meta["num_window_events"] = format_num(
            num_window_events, num_window_events_raw,
        )
        meta["num_screenshots"] = format_num(
            num_screenshots, num_screenshots_raw,
        )

        duration = input_events[-1].timestamp - input_events[0].timestamp
        if len(input_events) > 1:
            assert duration > 0, duration
        meta["duration"] = format_num(duration, duration_raw)

    end_time = time.time()
    duration = end_time - start_time
    logger.info(f"{duration=}")
    #print(f"\n\n\n\ndwagdwag{len(input_events)}\n\n\n\n")

    return input_events  # , window_events, screenshots


def make_parent_event(child, extra=None):
    # TODO: record which process_fn created the parent event
    event_dict = {
        # TODO: set parent event to child timestamp?
        #"timestamp": child.timestamp,
        "recording_timestamp": child.recording_timestamp,
        "window_event_timestamp": child.window_event_timestamp,
        "screenshot_timestamp": child.screenshot_timestamp,
        "recording": child.recording,
        "window_event": child.window_event,
        "screenshot": child.screenshot,
    }
    extra = extra or {}
    for key, val in extra.items():
        event_dict[key] = val
    return InputEvent(**event_dict)


def merge_consecutive_mouse_move_events(events, by_diff_distance=True):
    """Merge consecutive mouse move events into a single move event"""

    _all_slowdowns = []


    def is_target_event(event, state):
        return event.name == "move"


    def get_merged_events(
        to_merge,
        state,
        distance_threshold=1,

        # Minimum number of consecutive events (in which the distance between
        # the cursor and the nearest non-zero diff pixel is greater than
        # distance_threshold) in order to result in a separate parent event.
        # Larger values merge more events under a single parent.
        # TODO: verify logic is correct (test)
        # TODO: compute, e.g. as a function of diff and/or cursor velocity?
        min_idx_delta=5,  # 100
    ):
        N = len(to_merge)
        # (inclusive, exclusive)
        group_idx_tups = [(0, N)]
        if by_diff_distance:
            width_ratio, height_ratio = get_scale_ratios(to_merge[0])
            close_idxs = []
            # TODO: improve performance, e.g. vectorization, resizing
            _all_dts = []
            for idx, event in enumerate(to_merge):
                cursor_position = (
                    event.mouse_y * height_ratio,
                    event.mouse_x * width_ratio,
                )
                diff_mask = event.screenshot.diff_mask

                _ts = [time.perf_counter()]
                # ~99x slowdown
                if np.any(diff_mask):
                    _ts.append(time.perf_counter())

                    # TODO: compare with https://logicatcore.github.io/2020-08-13-sparse-image-coordinates/

                    # ~247x slowdown
                    diff_positions = np.argwhere(diff_mask)
                    _ts.append(time.perf_counter())

                    # ~6x slowdown
                    distances = distance.cdist(
                        [cursor_position], diff_positions,
                    )
                    _ts.append(time.perf_counter())

                    # ~1x slowdown
                    min_distance = distances.min()
                    _ts.append(time.perf_counter())
                    _dts = np.diff(_ts)
                    _all_dts.append(_dts)

                    logger.info(f"{min_distance=}")
                    if min_distance <= distance_threshold:
                        close_idxs.append(idx)

            if _all_dts:
                _all_dts = np.array(_all_dts)
                _slowdowns = _all_dts.mean(axis=0) / _all_dts.mean(axis=0).min()
                _all_slowdowns.append(_slowdowns)
                _mean_slowdowns = np.mean(_all_slowdowns, axis=0)
                logger.info(f"{_mean_slowdowns=}")

            if close_idxs:
                idx_deltas = np.diff(close_idxs)
                min_idx_delta_idxs = np.argwhere(
                    idx_deltas >= min_idx_delta
                ).flatten().tolist()
                group_idxs = np.array(close_idxs)[min_idx_delta_idxs].tolist()
                prefix = [0] if not group_idxs or group_idxs[0] != 0 else []
                suffix = [N] if not group_idxs or group_idxs[-1] != N else []
                group_boundary_idxs = prefix + group_idxs + suffix
                logger.debug(f"{close_idxs=}")
                logger.debug(f"{idx_deltas=}")
                logger.debug(f"{min_idx_delta_idxs=}")
                logger.debug(f"{group_idxs=}")
                logger.debug(f"{group_boundary_idxs=}")
                group_idx_tups = [
                    (start_idx, end_idx)
                    for start_idx, end_idx in zip(
                        group_boundary_idxs, group_boundary_idxs[1:]
                    )
                ]
        logger.debug(f"{group_idx_tups=}")
        merged_events = []
        for start_idx, end_idx in group_idx_tups:
            children = to_merge[start_idx:end_idx]
            # TODO: consolidate pattern with merge_consecutive_keyboard_events
            if len(children) == 1:
                # TODO: test
                event = children[0]
                event.timestamp -= state["dt"]
            else:
                first_child = children[0]
                last_child = children[-1]
                event = make_parent_event(
                    first_child,
                    {
                        "name": "move",
                        "mouse_x": last_child.mouse_x,
                        "mouse_y": last_child.mouse_y,
                        "timestamp": first_child.timestamp - state["dt"],
                        "children": children,
                    },
                )
                state["dt"] += last_child.timestamp - first_child.timestamp
            merged_events.append(event)
        logger.debug(f"{len(group_idx_tups)=}")
        logger.debug(f"{len(merged_events)=}")
        return merged_events


    return merge_consecutive_input_events(
        "mouse_move", events, is_target_event, get_merged_events,
    )


def merge_consecutive_mouse_scroll_events(events):
    """Merge consecutive mouse scroll events into a single scroll event"""


    def is_target_event(event, state):
        return event.name == "scroll"


    def get_merged_events(to_merge, state):
        state["dt"] += (to_merge[-1].timestamp - to_merge[0].timestamp)
        mouse_dx = sum(event.mouse_dx for event in to_merge)
        mouse_dy = sum(event.mouse_dy for event in to_merge)
        merged_event = to_merge[-1]
        merged_event.timestamp -= state["dt"]
        merged_event.mouse_dx = mouse_dx
        merged_event.mouse_dy = mouse_dy
        return [merged_event]


    return merge_consecutive_input_events(
        "mouse_scroll", events, is_target_event, get_merged_events,
    )


def merge_consecutive_mouse_click_events(events):
    """Merge consecutive mouse click events into a single doubleclick event"""


    def get_recording_attr(event, attr_name, fallback):
        attr = getattr(event.recording, attr_name) if event.recording else None
        if attr is None:
            fallback_value = fallback()
            logger.warning(f"{attr=} for {attr_name=}; using {fallback_value=}")
            attr = fallback_value
        return attr


    def is_target_event(event, state):
        # TODO: parametrize button name
        return event.name == "click" and event.mouse_button_name == "left"


    def get_timestamp_mappings(to_merge):
        double_click_distance = get_recording_attr(
            to_merge[0],
            "double_click_distance_pixels",
            get_double_click_distance_pixels,
        )
        logger.info(f"{double_click_distance=}")
        double_click_interval = get_recording_attr(
            to_merge[0],
            "double_click_interval_seconds",
            get_double_click_interval_seconds,
        )
        logger.info(f"{double_click_interval=}")
        press_to_press_t = {}
        press_to_release_t = {}
        prev_pressed_event = None
        for event in to_merge:
            if event.mouse_pressed:
                if prev_pressed_event:
                    dt = event.timestamp - prev_pressed_event.timestamp
                    dx = abs(event.mouse_x - prev_pressed_event.mouse_x)
                    dy = abs(event.mouse_y - prev_pressed_event.mouse_y)
                    if (
                        dt <= double_click_interval and
                        dx <= double_click_distance and
                        dy <= double_click_distance
                    ):
                        press_to_press_t[prev_pressed_event.timestamp] = (
                            event.timestamp
                        )
                prev_pressed_event = event
            elif prev_pressed_event:
                if prev_pressed_event.timestamp in press_to_release_t:
                    # should never happen
                    logger.warning("consecutive mouse release events")
                press_to_release_t[prev_pressed_event.timestamp] = (
                    event.timestamp
                )
        return press_to_press_t, press_to_release_t


    def get_merged_events(to_merge, state):
        press_to_press_t, press_to_release_t = get_timestamp_mappings(to_merge)
        t_to_event = {
            event.timestamp: event
            for event in to_merge
        }
        merged = []
        skip_timestamps = set()
        for event in to_merge:
            if event.timestamp in skip_timestamps:
                logger.debug(f"skipping {event.timestamp=}")
                continue
            if event.timestamp in press_to_press_t:
                # convert to doubleclick
                release_t = press_to_release_t[event.timestamp]
                next_press_t = press_to_press_t[event.timestamp]
                next_release_t = press_to_release_t[next_press_t]
                skip_timestamps.add(release_t)
                skip_timestamps.add(next_press_t)
                skip_timestamps.add(next_release_t)
                state["dt"] += (next_release_t - event.timestamp)
                event = make_parent_event(
                    event,
                    {
                        "name": "doubleclick",
                        "timestamp": next_release_t,
                        "mouse_x": event.mouse_x,
                        "mouse_y": event.mouse_y,
                        "mouse_button_name": event.mouse_button_name,
                        "children": [
                            event,
                            t_to_event[release_t],
                            t_to_event[next_press_t],
                            t_to_event[next_release_t],
                        ],
                    },
                )
            elif event.timestamp in press_to_release_t:
                # convert to singleclick
                release_t = press_to_release_t[event.timestamp]
                skip_timestamps.add(release_t)
                state["dt"] += (release_t - event.timestamp)
                event = make_parent_event(
                    event,
                    {
                        "name": "singleclick",
                        "timestamp": release_t,
                        "mouse_x": event.mouse_x,
                        "mouse_y": event.mouse_y,
                        "mouse_button_name": event.mouse_button_name,
                        "children": [
                            event,
                            t_to_event[release_t],
                        ],
                    },
                )
            event.timestamp -= state["dt"]
            merged.append(event)
        return merged


    return merge_consecutive_input_events(
        "mouse_click", events, is_target_event, get_merged_events,
    )


def merge_consecutive_keyboard_events(events, group_named_keys=True):
    """Merge consecutive keyboard char press events into a single press event"""


    def is_target_event(event, state):
        is_target_event = bool(event.key)
        logger.debug(f"{is_target_event=} {event=}")
        return is_target_event


    def get_group_idx_tups(to_merge):
        pressed_keys = set()
        was_pressed = False
        start_idx = 0
        group_idx_tups = []
        for event_idx, event in enumerate(to_merge):
            assert event.name in ("press", "release"), event
            if event.key_name:
                if event.name == "press":
                    if event.key in pressed_keys:
                        logger.warning(
                            f"{event.key=} already in {pressed_keys=}"
                        )
                    else:
                        pressed_keys.add(event.key)
                elif event.name == "release":
                    if event.key not in pressed_keys:
                        logger.warning(
                            f"{event.key} not in {pressed_keys=}"
                        )
                    else:
                        pressed_keys.remove(event.key)
            is_pressed = bool(pressed_keys)
            group_end = was_pressed and not is_pressed
            group_start = is_pressed and not was_pressed
            logger.debug(
                f"{event_idx=} {pressed_keys=} {is_pressed=} {was_pressed=} "
                f"{group_start=} {group_end=}"
            )
            if group_start or group_end:
                end_idx = event_idx + int(group_end)
                if end_idx > start_idx:
                    group_idx_tups.append((start_idx, end_idx))
                    logger.debug(f"{group_idx_tups=}")
                start_idx = end_idx
            was_pressed = is_pressed
        if start_idx < len(to_merge) - 1:
            # TODO: test
            group_idx_tups.append((start_idx, len(to_merge)))
        logger.info(f"{len(to_merge)=} {group_idx_tups=}")
        return group_idx_tups


    def get_merged_events(to_merge, state):
        if group_named_keys:
            group_idx_tups = get_group_idx_tups(to_merge)
        else:
            group_idx_tups = [(0, len(to_merge))]
        merged_events = []
        for start_idx, end_idx in group_idx_tups:
            children = to_merge[start_idx:end_idx]
            # TODO: consolidate pattern with merge_consecutive_mouse_move_events
            if len(children) == 1:
                # TODO: test
                event = children[0]
                event.timestamp -= state["dt"]
            else:
                first_child = children[0]
                last_child = children[-1]
                merged_event = make_parent_event(
                    first_child,
                    {
                        "name": "type",
                        "timestamp": first_child.timestamp - state["dt"],
                        "children": children,
                    },
                )
                state["dt"] += (last_child.timestamp - first_child.timestamp)
            merged_events.append(merged_event)
        return merged_events

    return merge_consecutive_input_events(
        "keyboard", events, is_target_event, get_merged_events,
    )


def remove_redundant_mouse_move_events(events):
    """Remove mouse move events that don't change the mouse position"""


    def is_target_event(event, state):
        return event.name in ("click", "move")


    def is_same_pos(e0, e1):
        if not all([e0, e1]):
            return False
        for attr in ("mouse_x", "mouse_y"):
            val0 = getattr(e0, attr)
            val1 = getattr(e1, attr)
            if val0 != val1:
                return False
        return True


    def should_discard(event, prev_event, next_event):
        return (
            event.name == "move" and (
                is_same_pos(prev_event, event) or
                is_same_pos(event, next_event)
            )
        )


    def get_merged_events(to_merge, state):
        to_merge = [None, *to_merge, None]
        merged_events = []
        dts = []
        children = []
        for idx, (prev_event, event, next_event) in enumerate(zip(
            to_merge, to_merge[1:], to_merge[2:],
        )):
            if should_discard(event, prev_event, next_event):
                if prev_event:
                    dt = event.timestamp - prev_event.timestamp
                else:
                    dt = next_event.timestamp - event.timestamp
                state["dt"] += dt
                children.append(event)
            else:
                dts.append(state["dt"])
                if children:
                    event.children = children
                    children = []
                merged_events.append(event)

        # update timestamps (doing this in the previous loop double counts)
        assert len(dts) == len(merged_events), (len(dts), len(merged_events))
        for event, dt in zip(merged_events, dts):
            event.timestamp -= dt

        return merged_events


    return merge_consecutive_input_events(
        "redundant_mouse_move", events, is_target_event, get_merged_events,
    )


def merge_consecutive_input_events(
    name, events, is_target_event, get_merged_events,
):
    """Merge consecutive input events into a single event"""

    num_events_before = len(events)
    state = {"dt": 0}
    rval = []
    to_merge = []


    def include_merged_events(to_merge):
        merged_events = get_merged_events(to_merge, state)
        rval.extend(merged_events)
        to_merge.clear()


    for event in events:
        assert event.name in MOUSE_EVENTS + KEY_EVENTS, event
        if is_target_event(event, state):
            to_merge.append(event)
        else:
            if to_merge:
                include_merged_events(to_merge)
            event.timestamp -= state["dt"]
            rval.append(event)

    if to_merge:
        include_merged_events(to_merge)

    num_events_after = len(rval)
    num_events_removed = num_events_before - num_events_after
    logger.info(f"{name=} {num_events_removed=}")

    return rval


def discard_unused_events(
    referred_events, input_events, referred_timestamp_key,
):
    referred_event_timestamps = set([
        getattr(input_event, referred_timestamp_key)
        for input_event in input_events
    ])
    num_referred_events_before = len(referred_events)
    referred_events = [
        referred_event
        for referred_event in referred_events
        if referred_event.timestamp in referred_event_timestamps
    ]
    num_referred_events_after = len(referred_events)
    num_referred_events_removed = (
        num_referred_events_before - num_referred_events_after
    )
    logger.info(f"{referred_timestamp_key=} {num_referred_events_removed=}")
    return referred_events


def process_events(input_events, window_events, screenshots):
    num_input_events = len(input_events)
    num_window_events = len(window_events)
    num_screenshots = len(screenshots)
    num_total = num_input_events + num_window_events + num_screenshots
    logger.info(
        f"before {num_input_events=} {num_window_events=} {num_screenshots=} "
        f"{num_total=}"
    )
    process_fns = [
        merge_consecutive_keyboard_events,
        merge_consecutive_mouse_move_events,
        merge_consecutive_mouse_scroll_events,
        remove_redundant_mouse_move_events,
        merge_consecutive_mouse_click_events,
    ]
    for process_fn in process_fns:
        input_events = process_fn(input_events)
        # TODO: keep events in which window_event_timestamp is updated
        for prev_event, event in zip(input_events, input_events[1:]):
            try:
                assert prev_event.timestamp <= event.timestamp, (
                    process_fn, prev_event, event,
                )
            except AssertionError as exc:
                logger.exception(exc)
                import ipdb; ipdb.set_trace()
        window_events = discard_unused_events(
            window_events, input_events, "window_event_timestamp",
        )
        screenshots = discard_unused_events(
            screenshots, input_events, "screenshot_timestamp",
        )
    num_input_events_ = len(input_events)
    num_window_events_ = len(window_events)
    num_screenshots_ = len(screenshots)
    num_total_ = num_input_events_ + num_window_events_ + num_screenshots_
    pct_input_events = num_input_events_ / num_input_events
    pct_window_events = num_window_events_ / num_window_events
    pct_screenshots = num_screenshots_ / num_screenshots
    pct_total = num_total_ / num_total
    logger.info(
        f"after {num_input_events_=} {num_window_events_=} {num_screenshots_=} "
        f"{num_total=}"
    )
    logger.info(
        f"{pct_input_events=} {pct_window_events=} {pct_screenshots=} "
        f"{pct_total=}"

    )
    return input_events, window_events, screenshots
