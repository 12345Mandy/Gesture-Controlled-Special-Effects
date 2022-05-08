#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import copy
import argparse
from collections import Counter
from collections import deque

from skimage import img_as_float32

#  import pyautogui

import cv2 as cv
import numpy as np
import mediapipe as mp

from KazuhitoTakahashiUtils import CvFpsCalc
from model import KeyPointClassifier
from model import PointHistoryClassifier

from KazuhitoTakahashiUtils.helpers import *

import tensorflow as tf
import tensorflow_hub as hub

from point_art import *


selection_modes = {
        "select": 0, 
        "tunnel": 1, 
        "effect": 2, 
        "panaroma": 3, 
        }

def display_selection_mode(selection_mode, display_text): 
    selection_mode_found = False
    for a_key in selection_modes: 
        if (selection_mode == selection_modes["effect"]): 
            display_text += "1. ghibli\n2. cartoon\n3. point art\n4. avatar\n"
            break

        elif selection_mode == selection_modes[a_key]: 
            display_text += (a_key + "\n")
            selection_mode_found = True
            break
    if not selection_mode_found: 
        display_text += "Selection mode not found\n"

    return display_text

def add_text(frame, text): 
    font = cv.FONT_HERSHEY_SIMPLEX
    pos = (100, 200)
    org = (50, 50)
    fontScale = 2
    color = (255, 0, 0)
    thickness = 2


    y0, dy = 240, 80
    for i, line in enumerate(text.split('\n')):
        y = y0 + i*dy
        cv.putText(frame, line, (50, y ), font, fontScale, color, thickness)

    #  cv.putText(frame, text, pos, font, 
    #                 fontScale, color, thickness, cv.LINE_AA)
    return frame

def cartoon_effect(frame, color_change): 
    # prepare color

    if color_change:
        frame = cv.cvtColor(frame, cv.COLOR_BGR2RGB)

    img_color = cv.pyrDown(cv.pyrDown(frame))
    for _ in range(3):
        img_color = cv.bilateralFilter(img_color, 9, 9, 7)
    img_color = cv.pyrUp(cv.pyrUp(img_color))

    # prepare edges
    img_edges = cv.cvtColor(frame, cv.COLOR_RGB2GRAY)
    img_edges = cv.adaptiveThreshold(
        cv.medianBlur(img_edges, 7), 255,
        cv.ADAPTIVE_THRESH_MEAN_C, cv.THRESH_BINARY,
        9, 2,)
    img_edges = cv.cvtColor(img_edges, cv.COLOR_GRAY2RGB)

    # combine color and edges
    frame = cv.bitwise_and(img_color, img_edges)
    return frame

def tunnel_effect(image, landmark): 
    (h,w) = image.shape[:2]
    center = np.array([landmark[0], landmark[1]])
    radius = h / 2.5

    i,j = np.mgrid[0:h, 0:w]
    xymap = np.dstack([j,i]).astype(np.float32) # "identity" map

    # coordinates relative to center
    coords = (xymap - center)
    # distance to center
    dist = np.linalg.norm(coords, axis=2)
    # touch only what's outside of the circle
    mask = (dist >= radius)
    # project onto circle (calculate unit vectors, move onto circle, then back to top-left origin)
    xymap[mask] = coords[mask] / dist[mask,None] * radius + center

    out = cv.remap(image, map1=xymap, map2=None, interpolation=cv.INTER_LINEAR)
    return out

def drawing(image, point_history):
    pre = None
    for index, point in enumerate(point_history):
        if point[0] != 0 and point[1] != 0:
            if pre == None:
                pre = point
            else: 
                cv.line(image, pre, point, (200, 140, 30), 2)
                pre = point
    return image

def stylization_popup(stylization_model, frame, style_image): 
    temp_debug_image = frame
    temp_debug_image = tf.expand_dims(temp_debug_image, 0)
    temp_debug_image = img_as_float32(temp_debug_image)
    temp_debug_image = tf.convert_to_tensor(temp_debug_image)

    hello = stylization_model(temp_debug_image, style_image)
    hello = np.asarray(hello[0][0])
    cv.imshow("hello", hello)

def impressionism_popup(frame):
    impressionism = run_impressionistic_filter(frame, False)
    cv.imshow("impressionism", impressionism)

def main():

    in_selection_mode = False
    current_mode = 0

    panorama_mode = False
    cartoon_mode = False
    drawing_mode = False
    tunnel_mode = False
    segmentation_mode = False

    use_brect = True

    # camera preparation ###############################################################
    cap = cv.VideoCapture(0)

    mp_hands = mp.solutions.hands
    hands = mp_hands.Hands(
        static_image_mode=True,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.5,
    )

    if (panorama_mode): 
        panorama = cv.imread('panorama.png')
        view_start = 0
        view_shift_speed = 1000
        #  view_shift_speed = 400

    keypoint_classifier = KeyPointClassifier()
    point_history_classifier = PointHistoryClassifier()
    canvas = np.zeros((1, 1, 3))

    # read models ###########################################################
    with open('model/keypoint_classifier/keypoint_classifier_label.csv',
              encoding='utf-8-sig') as f:
        keypoint_classifier_labels = csv.reader(f)
        keypoint_classifier_labels = [
            row[0] for row in keypoint_classifier_labels
        ]
    with open(
            'model/point_history_classifier/point_history_classifier_label.csv',
            encoding='utf-8-sig') as f:
        point_history_classifier_labels = csv.reader(f)
        point_history_classifier_labels = [
            row[0] for row in point_history_classifier_labels
        ]

    stylization_model = hub.load("model/image_stylization")
    style_image_og = cv.cvtColor(cv.imread("assets/ghibli-style.png"), cv.COLOR_BGR2RGB)
    style_image_og = img_as_float32(style_image_og)
    style_image_og = tf.expand_dims(style_image_og, 0)

    # FPS calculation ########################################################
    cvFpsCalc = CvFpsCalc(buffer_len=10)

    # point & gesture history generation #################################################################
    history_length = 16
    point_history = deque(maxlen=history_length)
    finger_gesture_history = deque(maxlen=history_length)

    #  ########################################################################
    mode = 0

    selection_mode = selection_modes["select"]
    frame_num = 0

    while True:
        display_text = ""
        fps = cvFpsCalc.get()
        frame_num += 1

        # exit the program #################################################
        key = cv.waitKey(10)
        if key == 27:  # ESC
            break
        number, mode = select_mode(key, mode)

        # capture image #####################################################
        ret, image = cap.read()
        if not ret:
            break
        image = cv.flip(image, 1) 
        debug_image = copy.deepcopy(image)


        # check output #############################################################
        image = cv.cvtColor(image, cv.COLOR_BGR2RGB)

        image.flags.writeable = False
        results = hands.process(image)
        image.flags.writeable = True

        #  ####################################################################
        if results.multi_hand_landmarks is not None:
            for hand_landmarks, handedness in zip(results.multi_hand_landmarks,
                                                  results.multi_handedness):
                # 外接矩形の計算
                brect = calc_bounding_rect(debug_image, hand_landmarks)
                # ランドマークの計算
                landmark_list = calc_landmark_list(debug_image, hand_landmarks)

                # 相対座標・正規化座標への変換
                pre_processed_landmark_list = pre_process_landmark(
                    landmark_list)
                pre_processed_point_history_list = pre_process_point_history(
                    debug_image, point_history)
                logging_csv(number, mode, pre_processed_landmark_list,
                            pre_processed_point_history_list)

                hand_sign_id = keypoint_classifier(pre_processed_landmark_list)
                if (hand_sign_id == 6): 
                    hand_sign_id = 0

                #  print("hand_sign_id: ", hand_sign_id)

                #  if (hand_sign_id == 0): 
                #      view_start += view_shift_speed
                #      pyautogui.scroll(-5)
                #  elif (hand_sign_id == 1): 
                #      view_start -= view_shift_speed
                #      pyautogui.scroll(5)

                
                print(frame_num)
                if (selection_mode == selection_modes["select"] and hand_sign_id != 0): 
                    selection_mode = hand_sign_id
                elif (hand_sign_id == 0): 
                    if (frame_num % 50 < 12): 
                        display_text += "Entered selection mode!\nChoose a mode\n"
                        selection_mode = selection_modes["select"]
                else: 
                    if selection_mode == selection_modes["tunnel"]: 
                        debug_image = tunnel_effect(debug_image, landmark_list[9])
                    elif selection_mode == selection_modes["effect"]: 
                        if (hand_sign_id == 1): # ghibli stylization
                            stylization_popup(stylization_model, debug_image, style_image_og)
                        elif (hand_sign_id == 2): # cartoon
                            debug_image = cartoon_effect(debug_image, False)
                        elif (hand_sign_id == 3): # point art stylization
                            impressionism_popup(debug_image)
                        elif (hand_sign_id == 4): # avatar blue skin
                            debug_image = cartoon_effect(debug_image, True)
                    elif selection_mode == selection_modes["panaroma"]: 
                        if hand_sign_id == 2: 
                            if landmark_list[8][0] > point_history[-1][0]: 
                                view_start += view_shift_speed
                            else: 
                                view_start -= view_shift_speed


                
                

                #  print("in_selection_mode? ", in_selection_mode)
                #  print("current_mode: ", current_mode)
                #  print("hand_sign_id: ", hand_sign_id)

                #  if (hand_sign_id == 1): # cartoon
                #      debug_image = cartoon_effect(debug_image, color_change=False)
                #  elif (hand_sign_id == 2): # ghibli stylization
                #      stylization_popup(stylization_model, debug_image, style_image_og)
                #  elif (hand_sign_id == 3): # point art stylization
                #      impressionism_popup(debug_image)
                #  elif (hand_sign_id == 4): # avatar blue skin mode
                #      debug_image = cartoon_effect(debug_image, color_change=True)

                print("in_selection_mode? ", in_selection_mode)
                print("current_mode: ", current_mode)
                print("hand_sign_id: ", hand_sign_id)

                if hand_sign_id == 2:  
                    point_history.append(landmark_list[8])
                else:
                    point_history.append([0, 0])

                # gesture classification
                finger_gesture_id = 0
                point_history_len = len(pre_processed_point_history_list)
                if point_history_len == (history_length * 2):
                    finger_gesture_id = point_history_classifier(
                        pre_processed_point_history_list)

                # 直近検出の中で最多のジェスチャーIDを算出
                finger_gesture_history.append(finger_gesture_id)
                most_common_fg_id = Counter(
                    finger_gesture_history).most_common()

                # generate information
                debug_image = draw_bounding_rect(use_brect, debug_image, brect)
                debug_image = draw_landmarks(debug_image, landmark_list)
                debug_image = draw_info_text(
                    debug_image,
                    brect,
                    handedness,
                    keypoint_classifier_labels[hand_sign_id],
                    point_history_classifier_labels[most_common_fg_id[0][0]],
                )
        else:
            point_history.append([0, 0])

        debug_image = draw_info(debug_image, fps, mode, number)
        display_text = display_selection_mode(selection_mode, display_text)
        add_text(debug_image, display_text)


        # show image #############################################################
        if panorama_mode: 
            view_width = 5000
            view_start = max(0, view_start)
            panorama_in_view = panorama[:,view_start:view_start+view_width]
            cv.imshow('Hand Gesture Recognition', panorama_in_view)
        elif drawing_mode: 
            h, w, c = debug_image.shape
            canvas = cv.resize(canvas, (w, h))
            canvas = drawing(canvas, point_history)
            final = cv.addWeighted(canvas.astype('uint8'), 1, debug_image, 1, 0)
            cv.imshow('Hand Gesture Recognition', final)
        else: 
            cv.imshow('Hand Gesture Recognition', debug_image)

    cap.release()
    cv.destroyAllWindows()


if __name__ == '__main__':
    main()

