import pygame
import sys
import json
import math
from threading import Thread, current_thread
from threading import enumerate as get_active_threads
from time import sleep

import logging
import datetime
import random
import os
import queue

from constants import *

#logging.basicConfig(level=logging.DEBUG)
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("main")
#LOG.setLevel(logging.DEBUG)
LOG.setLevel(logging.INFO)

PATHFINDING_LOG = logging.getLogger("pathfinding")
#PATHFINDING_LOG.setLevel(logging.DEBUG)     	# may cause lag
PATHFINDING_LOG.setLevel(logging.INFO)

COIN_LOG = logging.getLogger("coin")
#COIN_LOG.setLevel(logging.DEBUG)
COIN_LOG.setLevel(logging.INFO)

MAP_LOG = logging.getLogger("map")
#MAP_LOG.setLevel(logging.DEBUG)
MAP_LOG.setLevel(logging.INFO)


pygame.init()
programIcon = pygame.image.load('pacman_icon.png')
pygame.display.set_icon(programIcon)

WIDTH, HEIGHT = 800, 600
global screen
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Pac-Man")

CAN_MOVE_EVENT = pygame.USEREVENT + 1



maze = []
spawn = []
size = []


screen_update_queue = queue.Queue()
maze_update_queue = queue.Queue()
config_update_queue = queue.Queue()

ghosts = {}
pending_respawns = {}
regenerate_item_threads = []
running = True
final_close = False


# do not change
highscore = 0
last_score = 0


# menu specific
menu_options = ["Start New Game", "Settings", "Exit"]
current_selection = 0
continued_game_option = False

settings_menu_options = ["Level:", "Zoom:"]
level_options = ["easy", "medium", "hard"]
zoom_options = ["0.5", "0.75", "1.0", "1.25", "1.5", "1.75", "2.0"]
saved_level_index = 0
saved_zoom_index = 2  # defaults to scale = 1


def resize_window(width, height):
    global screen
    screen = pygame.display.set_mode((width, height))

@DeprecationWarning
def get_pixel_color(x, y):
    try:
        data = screen.get_at((x, y))
    except IndexError:
        return False
    return data[0], data[1], data[2]

def pixel_to_block_pos(x, y):   # starting from 1x1
    return math.ceil(x / TILE_SIZE), math.ceil(y / TILE_SIZE)   # always round up

def block_pos_to_pixel(x, y, center_block=True):   # starting from 1x1
    if center_block:
        return int((x - 0.5) * TILE_SIZE), int((y - 0.5) * TILE_SIZE)     # correct the missing rounding (so that pixel is centered)
    else:
        return int(x * TILE_SIZE), int(y * TILE_SIZE)

class Player:
    def __init__(self, x, y, coins=0):
        self.x = x
        self.y = y
        self.speed = 1  # in blocks; int only; do not change unless map was designed to support different speed
        self.move_cooldown = 0.5   # in sec
        self.can_move = True
        self.is_alive = True
        self.radius = TILE_SIZE / 2
        self.coins = coins

        LOG.debug(f"player spawn: {self.x} {self.y}")

        spawn_block = get_block(x, y)
        if spawn_block == COIN_SYMBOL:      # if player spawns on coin
            update_block(x, y, EMPTY_SYMBOL)
            self.add_coins(COIN_VALUE)
            res = prepare_regeneration_item(COIN_SYMBOL, (x, y), self, COIN_RESPAWN_TIME)
            thread = Thread(target=regenerate_item, args=(*res,), daemon=True)      # "*": turns my tuple in positional arguments
            regenerate_item_threads.append(thread)
            thread.start()

    def draw(self, screen):
        pygame.draw.circle(screen, PLAYER_COLOR, block_pos_to_pixel(self.x, self.y), self.radius)

    def move(self, dx, dy):
        self.x += dx * self.speed
        self.y += dy * self.speed
        LOG.debug(f"player move: {self.x} {self.y}")

    def future_pos(self, dx, dy, x=None, y=None):
        if x is None:
            x = self.x
        if y is None:
            y = self.y
        x = (dx * self.speed) + x
        y = (dy * self.speed) + y
        return x, y

    def add_coins(self, coins=1):
        self.coins += coins

    def kill(self):
        global running
        LOG.info("Player killed")
        self.is_alive = False

        if self.coins > read_config()["userdata"]["score"][lvl]["highscore"]:
            config_update_queue.put({
                "userdata.current_play.is_alive": self.is_alive,
                f"userdata.score.{lvl}.last_score": self.coins,
                f"userdata.score.{lvl}.highscore": self.coins
            })
        else:
            config_update_queue.put({
                "userdata.current_play.is_alive": self.is_alive,
                f"userdata.score.{lvl}.last_score": self.coins
            })

        screen_update_queue.put(None)
        running = False

class Ghost:
    def __init__(self, id, x, y, player, old_symbol):
        self.id = id
        self.x = x
        self.y = y
        self.speed = 1      # in blocks; int only; do not change unless map was designed to support different speed
        self.cooldown = 0.5       # in sec
        self.player = player
        self.spawn_lock_time = 5    # in sec (int); >= 1
        self.old_symbol = old_symbol

        ghosts[id] = self

        LOG.debug(f"ghost spawn: {self.x} {self.y}")

    def future_pos(self, dx, dy, x=None, y=None): # can use a custom starting point
        if x is None:
            x = self.x
        if y is None:
            y = self.y
        x = (dx * self.speed) + x
        y = (dy * self.speed) + y
        return x, y

    def auto_move(self):
        LOG.debug(f"ghost{self.id} calculating next step")
        next_step = self.get_next_step()
        LOG.debug(f"ghost{self.id} next step (direction): {next_step}")

        if next_step is not False:  # not using "if next_step != False" here since it will trigger when having 0
            dx, dy = 0, 0
            if next_step == 0:     # O
                dx = 1 * self.speed
            elif next_step == 1:   # W
                dx = -1 * self.speed
            elif next_step == 2:   # S
                dy = 1 * self.speed
            elif next_step == 3:   # N
                dy = -1 * self.speed

            return self.move(dx, dy)
        return False

    def move(self, dx, dy):

        future_pos = self.future_pos(dx, dy)
        old_symbol = get_block(future_pos[0], future_pos[1])

        if old_symbol == GHOST_SYMBOL:      # if there was another ghost it needs to wait so that it can detect the static entity on the block
            return False

        self.x += dx * self.speed
        self.y += dy * self.speed

        LOG.debug(f"ghost{self.id} move: {self.x} {self.y}")

        if self.x == self.player.x and self.y == self.player.y:
            self.player.kill()

        return old_symbol

    def get_next_step(self):
        path = find_shortest_way(self, (self.x, self.y), (self.player.x, self.player.y))
        if path == False or len(path) == 0:
            return False
        return path[0]

def game_exit():
    pygame.quit()
    sys.exit()

def read_config():
    if not os.path.exists(CONFIG_FILE):
        LOG.critical("Couldn't find config file")
        game_exit()

    with open(CONFIG_FILE, 'r') as f:
        c = json.load(f)
        return c

def override_value(json_obj, path, new_value):  # function made by AI   # TODO: understand
    path_parts = path.split('.')
    current_obj = json_obj
    for part in path_parts[:-1]:
        current_obj = current_obj[part]
    current_obj[path_parts[-1]] = new_value
    return json_obj

def update_config_direct(items):        # only use if sure that the file isn't used elsewhere at the same time
    if not os.path.exists(CONFIG_FILE):
        LOG.critical("Couldn't find config file")
        game_exit()

    config = read_config()
    for path, val in items.items():
        config = override_value(config, path, val)

    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def update_config(player):
    last_save = False
    while True:
        items = config_update_queue.get()
        if items:
            update_config_direct(items)
            config_update_queue.task_done() # needs to be here for some reason

        if final_close and not last_save:
            last_save = True
            LOG.info("doing final save, please wait")
            save(player, False)    # save last time
        
        if last_save:
            LOG.info("Saved game")
            return
        
        
def load_map(c, new_game):
    global maze
    global spawn
    global size

    if c["userdata"]["current_play"]["is_alive"] and not new_game:   # continue game
        maze = c["userdata"]["current_play"]["maze"]
        spawn = c["userdata"]["current_play"]["position"]

    else:                                           # new game
        maze = c["maps"][lvl]["data"]
        spawn_data = c["maps"][lvl]["spawn"]
        if type(spawn_data) == list:
            spawn = spawn_data
        elif type(spawn_data) == str:
            if spawn_data == "random":
                spawn = get_random_spawn_block([EMPTY_SYMBOL, COIN_SYMBOL])


    size = block_pos_to_pixel(len(maze[0]), len(maze), center_block=False)
    resize_window(size[0], size[1])

def draw_maze(screen, maze, player, init=False):
    maze_update_queue.join()    # waits for maze updates to be completed
    for y, row in enumerate(maze):
        for x, char in enumerate(row):
            if char == WALL_SYMBOL:
                pygame.draw.rect(screen, WALL_COLOR, (x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE))
            elif char == COIN_SYMBOL:
                pygame.draw.circle(screen, COIN_COLOR, (x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2), COIN_SIZE)
            elif char == BIGCOIN_SYMBOL:
                pygame.draw.circle(screen, BIGCOIN_COLOR, (x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2), BIGCOIN_SIZE)
            elif char == GHOST_SYMBOL:
                pygame.draw.circle(screen, GHOST_COLOR, (x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2), GHOST_SIZE)
            
                leg_width = GHOST_SIZE // 2
                leg_height = (GHOST_SIZE // 2) + (GHOST_SIZE / 2)
                left_leg_rect = pygame.Rect(x * TILE_SIZE + TILE_SIZE // 2 - GHOST_SIZE, y * TILE_SIZE + TILE_SIZE // 2, leg_width, leg_height)
                right_leg_rect = pygame.Rect(x * TILE_SIZE + TILE_SIZE // 2 + GHOST_SIZE - leg_width, y * TILE_SIZE + TILE_SIZE // 2, leg_width, leg_height)
                pygame.draw.rect(screen, GHOST_COLOR, left_leg_rect)
                pygame.draw.rect(screen, GHOST_COLOR, right_leg_rect)

                if init:
                    summon_ghost(player, (x+1,y+1))

def get_direction(old_x, old_y, new_x, new_y):      # debug function
    if old_x == new_x and old_y < new_y:
        return "S"
    elif old_x == new_x and old_y > new_y:
        return "N"
    elif old_y == new_y and old_x < new_x:
        return "O"
    elif old_y == new_y and old_x > new_x:
        return "W"


def check_collision(entity, dx, dy, x=None, y=None):        # reworked to check based on block from maze list instead of pixel color
    future_pos = entity.future_pos(dx, dy, x, y)
    try:
        entity_on_block = maze[future_pos[1]-1][future_pos[0]-1]
    except IndexError:
        return (False, False)

    if entity_on_block == WALL_SYMBOL:
        return (False, False)
    elif entity_on_block == EMPTY_SYMBOL:
        return (True, False)
    
    return (True, entity_on_block)


def update_screen(player):

    init = True
    while True:
        debug = screen_update_queue.get()           # callbacks making sure this function isn't called twice at the same time


        if running:
            screen.fill(BLACK)      # throws error if called after exit
        elif debug == "exit":       # only exit on custom call
            return

        draw_maze(screen, maze, player, init)

        if player.is_alive:
            player.draw(screen)

        display_coins(player.coins)

        if debug != None:
            debug()

        pygame.display.flip()   # update display

        init = False
        screen_update_queue.task_done()

def get_pending_respawn_key():
    if len(pending_respawns) > 0:
        return max(pending_respawns.keys()) + 1
    else:
        return 0

def prepare_regeneration_item(entity, pos, player, in_future=0):
    key = get_pending_respawn_key()
    pending_respawns[key] = [entity, pos, in_future]
    return (entity, pos, player, key, in_future)

def regenerate_item(entity, pos, player, key, in_future=0):

    while True:

        if in_future != 0:
            
            for i in range(in_future):
                if not running:
                    return
                pending_respawns[key] = [entity, pos, in_future-i]  # save remaining time to config, so that on continued game the timer won't reset
                sleep(1)      
        
        if player.x != pos[0] or player.y != pos[1]:        # prevents afk coin farm
            if get_block(pos[0], pos[1]) == EMPTY_SYMBOL:   # prevents to spawn multiple entities on single block
                update_block(pos[0], pos[1], entity)
                if entity == COIN_SYMBOL:
                    COIN_LOG.debug(f"respawned coin at {pos[0]},{pos[1]}")
                del pending_respawns[key]
                try:
                    regenerate_item_threads.remove(current_thread())
                except: # probably already removed, ignoring
                    pass
                return
        COIN_LOG.debug(f"coin at {pos[0]},{pos[1]} couldn't respawn")

def update_maze_block():

    while True:
        items = maze_update_queue.get()           # callbacks making sure this function isn't called twice at the same time
        if len(items) == 3:
            x_block, y_block, updated_block = items[0], items[1], items[2]

            row = maze[y_block-1]
            new_row = row[:x_block-1] + updated_block + row[x_block:]
            maze[y_block-1] = new_row

        elif len(items) == 6:
            x_block, y_block, updated_block, x_block2, y_block2, updated_block2 = items[0], items[1], items[2], items[3], items[4], items[5]

            row = maze[y_block-1]
            new_row = row[:x_block-1] + updated_block + row[x_block:]
            maze[y_block-1] = new_row

            row = maze[y_block2-1]
            new_row = row[:x_block2-1] + updated_block2 + row[x_block2:]
            maze[y_block2-1] = new_row
        
        if MAP_LOG.level == logging.DEBUG:  # may cause lag otherwise
            maze_str = '\n'.join(maze)
            MAP_LOG.debug(f"updated maze:\n{maze_str}\n")

        maze_update_queue.task_done()

        if not running and len(items) == 0: # only exit on custom call because the other calls mostly .join() the callback which will result in a freeze
            return

def update_block(x_block, y_block, updated_block):
    maze_update_queue.put((x_block, y_block, updated_block))
    screen_update_queue.put(None)

def swap_block(x_block, y_block, updated_block, x_block2, y_block2, updated_block2):
    maze_update_queue.put((x_block, y_block, updated_block, x_block2, y_block2, updated_block2))
    screen_update_queue.put(None)

def get_block(x_block, y_block):
    maze_update_queue.join()    # waits for maze updates to be completed
    return maze[y_block-1][x_block-1]

def entity_collision_handler(player, entity):
    if entity == COIN_SYMBOL:
        
        pos_x, pos_y = player.x, player.y
        update_block(pos_x, pos_y, EMPTY_SYMBOL)

        player.add_coins(COIN_VALUE)
        COIN_LOG.debug(f"collected coin at {pos_x},{pos_y}")

        res = prepare_regeneration_item(entity, (pos_x, pos_y), player, COIN_RESPAWN_TIME)
        thread = Thread(target=regenerate_item, args=(*res,), daemon=True)      # "*": turns my tuple in positional arguments
        regenerate_item_threads.append(thread)
        thread.start()

    elif entity == BIGCOIN_SYMBOL:
        pos_x, pos_y = player.x, player.y
        update_block(pos_x, pos_y, EMPTY_SYMBOL)

        player.add_coins(BIGCOIN_VALUE)
        COIN_LOG.debug(f"collected bigcoin at {pos_x},{pos_y}")

    elif entity == GHOST_SYMBOL:
        player.kill()


def display_coins(count):
    text = COIN_DISPLAY_FONT.render(f'Coins: {count}', True, WHITE)
    text_rect = text.get_rect()
    text_rect.topright = (screen.get_width() - 40, 5)

    highscore_text = COIN_DISPLAY_FONT.render(f'Highscore: {highscore}', True, WHITE)
    highscore_text_rect = highscore_text.get_rect()
    highscore_text_rect.center = (int(screen.get_width()/2), 5 + (highscore_text_rect.height / 2))

    last_score_text = COIN_DISPLAY_FONT.render(f'Last Score: {last_score}', True, WHITE)
    last_score_text_rect = last_score_text.get_rect()
    last_score_text_rect.topleft = (40, 5)

    screen.blit(text, text_rect)
    screen.blit(highscore_text, highscore_text_rect)
    screen.blit(last_score_text, last_score_text_rect)


def ghost_handler(ghost):

    for i in range(ghost.spawn_lock_time):
        if not running:
            return
        sleep(1)

    old_symbol = ghost.old_symbol
    old_x, old_y = ghost.x, ghost.y

    while True:
        
        if running:

            begin_time = datetime.datetime.now()
            current_symbol = ghost.auto_move()
            current_x = ghost.x
            current_y = ghost.y
            end_time = datetime.datetime.now()
            took = end_time - begin_time
            took_ms = took.microseconds / 1000
            LOG.debug(f"ghost{ghost.id} time to calculate way: {took_ms}")      # TODO: set a fixed allowed time for calculation, otherwise slower computers have an advantage

            if old_symbol != None:
                if current_symbol != False:
                    swap_block(ghost.x, ghost.y, GHOST_SYMBOL, old_x, old_y, old_symbol)
                    old_symbol = current_symbol
                    ghost.old_symbol = old_symbol
                    old_x = current_x
                    old_y = current_y

            else:
                LOG.error(f"old_symbol not found: {old_symbol}")


            sleep(ghost.cooldown) # TODO: decrease based on time

        else:
            return

def get_next_ghost_id():
    return len(ghosts)

def get_random_spawn_block(allowed_blocks, player = None):
    empty_blocks = []

    for y, row in enumerate(maze):
        for x, char in enumerate(row):
            if get_block(x, y) in allowed_blocks:
                if player != None:
                    if not player.x == x and not player.y == y:
                        empty_blocks.append((x, y))
                else:
                    empty_blocks.append((x, y))

    if len(empty_blocks) > 0:
        return random.choice(empty_blocks)
    LOG.debug("no empty block was found")
    return False

def count_symbol(symbol):
    i = 0
    for y, row in enumerate(maze):
        for x, char in enumerate(row):
            if get_block(x, y) == symbol:
                i+= 1
    return i



def summon_ghost(target_player, spawn=False):
    ghost_id = get_next_ghost_id()
    LOG.info(f"spawning ghost{ghost_id}")
    if not spawn:
        spawn = get_random_spawn_block([EMPTY_SYMBOL, COIN_SYMBOL], target_player)       # TODO: condition to spawn ghosts with minimum distance to player
    if spawn != False:
        
        old_symbol = get_block(spawn[0], spawn[1])
        if not old_symbol == GHOST_SYMBOL:  
            update_block(spawn[0], spawn[1], GHOST_SYMBOL)
        else:   # means ghost exists already 
            old_symbol = EMPTY_SYMBOL   # make empty, if there was a coin previously the load_pending_respawns function will restore them (continued game)

        ghost = Ghost(ghost_id, spawn[0], spawn[1], target_player, old_symbol)
        ghost_thread = Thread(target=ghost_handler, args=(ghost,), daemon=True)  # exit or killed on exit
        ghost_thread.start()
        

def ghost_generator(player, continued_game=False):

    if not continued_game:

        if lvl == "easy":
            for i in range(2):
                summon_ghost(player)
        
        if lvl == "medium":
            for i in range(3):
                summon_ghost(player)

        if lvl == "hard":
            for i in range(4):
                summon_ghost(player)

    while True:
        if lvl == "easy":
            # TODO: if continued game: save remaining countdown and use that instead of new one, otherwise game can be stopped on right time to reset timing
            for i in range(60):
                if not running:
                    return
                sleep(1)
            summon_ghost(player)
        
        if lvl == "medium":
            for i in range(45):
                if not running:
                    return
                sleep(1)
            summon_ghost(player)

        if lvl == "hard":
            for i in range(30):
                if not running:
                    return
                sleep(1)
            summon_ghost(player)

def summon_bigcoin(player):
    random_block = get_random_spawn_block([EMPTY_SYMBOL, COIN_SYMBOL], player)
    symbol = get_block(random_block[0], random_block[1])

    update_block(random_block[0], random_block[1], BIGCOIN_SYMBOL)

    if symbol == COIN_SYMBOL:   # make sure the normal coin respawns again
        
        res = prepare_regeneration_item(COIN_SYMBOL, (random_block[0], random_block[1]), player, COIN_RESPAWN_TIME)
        thread = Thread(target=regenerate_item, args=(*res,), daemon=True)      # "*": turns my tuple in positional arguments
        thread.start()

def bigcoin_generator(player):
    while True:
        sleep_time = random.randint(BIGCOIN_SUMMON_MIN, BIGCOIN_SUMMON_MAX)
        for i in range(sleep_time):
            if not running:
                return
            sleep(1)
        if running:
            if count_symbol(BIGCOIN_SYMBOL) < BIGCOIN_SIMULTANEOUS_LIMIT:
                summon_bigcoin(player)
        else:
            return

def save(player, block=True):

    maze_update_queue.join()

    config_update_queue.put({
        "userdata.current_play.is_alive": player.is_alive,
        "userdata.current_play.score": player.coins,
        "userdata.current_play.position": [player.x, player.y],
        "userdata.current_play.maze": maze,
        "userdata.current_play.pending_respawns": pending_respawns,
        "userdata.current_play.lvl": lvl,
    })

    if block:
        config_update_queue.join()  # wait for update to complete
    

def auto_save(player):
    while True:
        
        sleep(AUTO_SAVE_INTERVAL)
        LOG.debug("saving ...")
        save(player)
        LOG.debug("saved")
        if final_close:
            config_update_queue.put(None)   # trigger for last save in case not done yet
            LOG.debug("exiting save thread ...")
            return


def check_all_directions(entity, pos):
    result = []
    dx, dy = 0, 0
    for _ in range(4):  # directions
        if dx == 0 and dy == 0:     # O
            dx = 1 * entity.speed
        elif dx == 1 and dy == 0:   # W
            dx = -1 * entity.speed
        elif dx == -1 and dy == 0:  # S
            dx = 0
            dy = 1 * entity.speed
        elif dx == 0 and dy == 1:   # N
            dy = -1 * entity.speed

        if check_collision(entity, dx, dy, pos[0], pos[1])[0]:
            result.append(entity.future_pos(dx, dy, x=pos[0], y=pos[1]))
        else:
            result.append(False)

    return result

# debug function: brings huge lag; use sleep to see better; don't move player meanwhile or it will override screen
# green ball:   normal way
# red ball:     crossing
def show_pathfinding(x, y, color):     
    screen_update_queue.put(lambda: pygame.draw.circle(screen, color, ((x-1) * TILE_SIZE + TILE_SIZE // 2, (y-1) * TILE_SIZE + TILE_SIZE // 2), COIN_SIZE))
    sleep(0.05)

# TODO: implement parallelization: with threads and multiprocessing
# activate visual debugging by uncommenting show_pathfinding twice 
def pathfinding(entity, ending_pos, res, current_pos, old_pos, last_was_corner, after_corners, hits, ways, current_way, steps=0):

    if len(hits) > 0:
        if steps > min(hits):
            return hits, ways

    if current_pos == ending_pos:
        PATHFINDING_LOG.debug("hit, steps: " + str(steps))
        hits.append(steps)
        ways.append(current_way.copy())
        return hits, ways

    if not res.count(False) > 1:
        last_was_corner = True
    else:
        last_was_corner = False

    original_after_corners = after_corners.copy()   # to get same effect like the steps counter has (resets automatically)
    original_current_way = current_way.copy()

    for idx, i in enumerate(res):
        if i != False and i not in after_corners and i != old_pos:     # avoids unusable blocks, does loop detection, avoids going back
            current_way.append(idx)     # store direction
            new_res = check_all_directions(entity, i)
            #print(steps, i, current_pos, new_res)
            if last_was_corner:
                #print("corner add", after_corners)
                after_corners.append(i)
                #show_pathfinding(i[0], i[1], RED)
            else:
                #show_pathfinding(i[0], i[1], GREEN)
                pass
            hits, ways = pathfinding(entity, ending_pos, new_res, i, current_pos, last_was_corner, after_corners, hits, ways, current_way, steps+1)
            current_way.pop()   # remove direction

    after_corners[:] = original_after_corners   # set the copied list
    current_way[:] = original_current_way

    return hits, ways

def find_shortest_way(entity, starting_pos, ending_pos):

    if starting_pos != ending_pos:

        res = check_all_directions(entity, starting_pos)
        hits, ways = pathfinding(entity, ending_pos, res, starting_pos, (0,0), False, [], [], [], [])
        if len(hits) == 0:
            PATHFINDING_LOG.debug("no way found")
            return False
        
        shortest_way_steps = min(hits)              # if multiple ways with same steps
        if lvl == "easy" or lvl == "medium":        # always use right way (from incoming direction)
            shortest_way = ways[hits.index(shortest_way_steps)]     
        elif lvl == "hard":                         # 50% chance on any way
            indexes = [index for index, value in enumerate(hits) if value == shortest_way_steps]
            random_index = random.choice(indexes)
            shortest_way = ways[random_index]

        PATHFINDING_LOG.debug("required steps: " + str(shortest_way_steps))
        PATHFINDING_LOG.debug("directions: " + str(shortest_way))

        return shortest_way
    return []

def load_pending_respawns(last_pending_respawns, player):
    for key, value in last_pending_respawns.items():
        res = prepare_regeneration_item(value[0], value[1], player, value[2])
        thread = Thread(target=regenerate_item, args=(*res,), daemon=True)      # "*": turns my tuple in positional arguments
        thread.start()

def handle_player_move(player, dx, dy):
    if dx != 0 or dy != 0:

        allowed, entity = check_collision(player, dx, dy)
        if allowed:
            player.move(dx, dy)
            if entity:
                entity_collision_handler(player, entity)

            screen_update_queue.put(None)
            player.can_move = False
            pygame.time.set_timer(CAN_MOVE_EVENT, int(player.move_cooldown * 1000))     # Sets a timer while player move cooldown

def cleanup(player):
    for ghost in ghosts.values():
        if ghost.old_symbol != EMPTY_SYMBOL:
            prepare_regeneration_item(ghost.old_symbol, (ghost.x, ghost.y), player, 1)  # try to restore that previous item every second


    

def start_game(new_game):
    global running
    global final_close
    global highscore
    global last_score
    global lvl
    global ghosts
    global pending_respawns
    global maze
    global size
    global regenerate_item_threads
    global screen_update_queue
    global maze_update_queue
    global config_update_queue
    global TILE_SIZE
    global GHOST_SIZE
    global COIN_SIZE
    global COIN_DISPLAY_FONT
    global BIGCOIN_SIZE
    global saved_level_index
    global saved_zoom_index
    global continued_game_option

    #maze = []      # can't override them here but they are anyways overriden when loading map
    #spawn = []
    #size = []

    screen_update_queue = queue.Queue()
    maze_update_queue = queue.Queue()
    config_update_queue = queue.Queue()

    ghosts = {}
    pending_respawns = {}
    regenerate_item_threads = []
    running = True
    final_close = False

    controlling = "both"    # "both": push or hold; "push": push only   - setting not needed because most likely never changed
    #lvl = "easy"            # "easy", "medium" or "hard" - just default here
    #scaling_factor = 1          # default

    LOG.info("starting game ...")
    LOG.info("loading config")
    c = read_config()
    lvl = c["userdata"]["settings"]["difficulty_set"]
    if lvl == "easy":
        saved_level_index = 0
    elif lvl == "medium":
        saved_level_index = 1
    elif lvl == "hard":
        saved_level_index = 2

    scaling_factor = c["userdata"]["settings"]["scaling_factor"]
    if scaling_factor == 0.5:
        saved_zoom_index = 0
    elif scaling_factor == 0.75:
        saved_zoom_index = 1
    elif scaling_factor == 1:
        saved_zoom_index = 2
    elif scaling_factor == 1.25:
        saved_zoom_index = 3
    elif scaling_factor == 1.5:
        saved_zoom_index = 4
    elif scaling_factor == 1.75:
        saved_zoom_index = 5
    elif scaling_factor == 2:
        saved_zoom_index = 6
    
    TILE_SIZE = DEFAULT_SIZE * scaling_factor # in pixel, can be modified for scaling
    GHOST_SIZE = TILE_SIZE / 2
    COIN_SIZE = TILE_SIZE / 6
    COIN_DISPLAY_FONT = pygame.font.Font(None, int(DEFAULT_SIZE * scaling_factor))  # scaling the font seems to work with scaling >= 0.5
    BIGCOIN_SIZE = TILE_SIZE / 4

    LOG.info("loading map")
    load_map(c, new_game)
    LOG.info("spawning player")
    
    if c["userdata"]["current_play"]["is_alive"] and not new_game:   # continue game
        coins = c["userdata"]["current_play"]["score"]
        player = Player(spawn[0], spawn[1], coins)
        load_pending_respawns(c["userdata"]["current_play"]["pending_respawns"], player)
        lvl = c["userdata"]["current_play"]["lvl"]
        ghost_generator_thread = Thread(target=ghost_generator, args=(player, True), daemon=True)
    else:
        player = Player(spawn[0], spawn[1])
        ghost_generator_thread = Thread(target=ghost_generator, args=(player,), daemon=True)

    highscore = c["userdata"]["score"][lvl]["highscore"]
    last_score = c["userdata"]["score"][lvl]["last_score"]
    

    display_coins(player.coins)

    update_config_thread = Thread(target=update_config, args=(player,), daemon=False)   # to make sure this thread doesn't get killed
    update_config_thread.start()

    update_screen_thread = Thread(target=update_screen, args=(player,), daemon=True)
    update_screen_thread.start()

    update_maze_thread = Thread(target=update_maze_block, daemon=True)
    update_maze_thread.start()

    screen_update_queue.put(None)   
    screen_update_queue.join()      # make sure to load maze once before ghosts spawn
    ghost_generator_thread.start()  

    bigcoin_generator_thread = Thread(target=bigcoin_generator, args=(player,), daemon=True)
    bigcoin_generator_thread.start()

    auto_save_thread = Thread(target=auto_save, args=(player,), daemon=False)   # to make sure this thread doesn't get killed
    auto_save_thread.start()

    screen_update_queue.put(None)

    def await_game_close():         # TODO: better way to make sure all threads are exited, all threads should be saved in (a) list/s and join them/send exit trigger
        global final_close
        ghost_generator_thread.join()   # stop ghost generation
        bigcoin_generator_thread.join() # stop bigcoin generation

        for thread in regenerate_item_threads:
            thread.join()

        # handle regenerate_item threads before exiting update_maze_thread
        maze_update_queue.put(())           # trigger exit
        update_maze_thread.join()

        screen_update_queue.put("exit")     # trigger exit
        update_screen_thread.join()
        cleanup(player)
        final_close = True
        update_config_thread.join()      
        auto_save_thread.join()         # wait for save exit

    player.can_move = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                await_game_close()
                game_exit()

            elif event.type == CAN_MOVE_EVENT:
                player.can_move = True
                pygame.time.set_timer(CAN_MOVE_EVENT, 0)  # Stop the timer

            if controlling == "push":
                if event.type == pygame.KEYDOWN and player.can_move and player.is_alive:
                    dx, dy = 0, 0
                    if event.key == pygame.K_DOWN:
                        dy = 1
                    elif event.key == pygame.K_UP:
                        dy = -1
                    elif event.key == pygame.K_LEFT:
                        dx = -1
                    elif event.key == pygame.K_RIGHT:
                        dx = 1  

                    handle_player_move(player, dx, dy)

        if controlling == "both":
            keys = pygame.key.get_pressed()
            if player.can_move and player.is_alive:
                dx, dy = 0, 0
                if keys[pygame.K_DOWN]:
                    dy = 1
                elif keys[pygame.K_UP]:
                    dy = -1
                elif keys[pygame.K_LEFT]:
                    dx = -1
                elif keys[pygame.K_RIGHT]:
                    dx = 1 

                handle_player_move(player, dx, dy)


    # assuming player was killed
    await_game_close()
    active_threads = get_active_threads()
    LOG.debug(f"active threads after game close: {active_threads}")
    if len(active_threads) > 1:     # one is always MainThread
        LOG.error("Can't go back to menu because of threads still being active")
        game_exit()

    resize_window(WIDTH, HEIGHT)
    reload_menu()
    return "Game Over!"


def draw_settings_menu():

    OPTIONS_FONT = pygame.font.Font(None, 50)

    screen.fill(BLUE)

    for i, option in enumerate(settings_menu_options):
        text = OPTIONS_FONT.render(option, True, WHITE)
        text_rect = text.get_rect(center=(WIDTH / 4, 50 + i * 150))
        screen.blit(text, text_rect)

    n = 0

    for i, level_option in enumerate(level_options):
        if n == current_selection:
            text = OPTIONS_FONT.render(level_option, True, YELLOW)
        elif i == saved_level_index:
            text = OPTIONS_FONT.render(level_option, True, GREEN)
        else:
            text = OPTIONS_FONT.render(level_option, True, WHITE)
        text_rect = text.get_rect(center=(WIDTH * 5/8, 50 + i * 50))
        screen.blit(text, text_rect)
        n += 1

    for i, zoom_option in enumerate(zoom_options):
        if n == current_selection:
            text = OPTIONS_FONT.render(zoom_option, True, YELLOW)
        elif i == saved_zoom_index:
            text = OPTIONS_FONT.render(zoom_option, True, GREEN)
        else:
            text = OPTIONS_FONT.render(zoom_option, True, WHITE)
        text_rect = text.get_rect(center=(WIDTH * 5/8, 200 + i * 50))
        screen.blit(text, text_rect)
        n += 1

    if n == current_selection:
        text = OPTIONS_FONT.render("Return", True, YELLOW)
    else:
        text = OPTIONS_FONT.render("Return", True, WHITE)
    
    text_rect = text.get_rect(center=(WIDTH * 5/8, 250 + (len(zoom_options)-1) * 50))
    screen.blit(text, text_rect)

    pygame.display.flip()

def save_settings(level, zoom):
    update_config_direct({
        "userdata.settings.scaling_factor": zoom,
        "userdata.settings.difficulty_set": level
    })


def settings_menu():
    global current_selection
    global saved_level_index    # TODO: bug: doesn't refresh on startup
    global saved_zoom_index 	        # same here

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                game_exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_UP:
                    if current_selection >= 1:
                        current_selection -= 1
                elif event.key == pygame.K_DOWN:
                    if current_selection <= (len(level_options) + len(zoom_options) + 1) - 2:   # one because of index, one for future move
                        current_selection += 1
                elif event.key == pygame.K_RETURN:
                    if current_selection >= 0 and current_selection < len(level_options):   # selection is within lvl options
                        saved_level_index = current_selection
                    elif current_selection >= len(level_options) and current_selection < (len(level_options) + len(zoom_options)):     # selection is within zoom options
                        saved_zoom_index = current_selection - len(level_options)
                    else:   # selection is return
                        save_settings(level_options[saved_level_index], float(zoom_options[saved_zoom_index]))
                        current_selection = 0
                        return

            draw_settings_menu()
            


def continued_game_possible():
    c = read_config()
    return c["userdata"]["current_play"]["is_alive"]

def reload_menu():
    global menu_options
    global continued_game_option
    continued_game_option = continued_game_possible()
    if continued_game_option:
        menu_options = ["Start New Game", "Continue Game", "Settings", "Exit"]
    else:
        menu_options = ["Start New Game", "Settings", "Exit"]
    

def draw_menu(extra_msg=None):
    screen.fill(BLUE)

    if extra_msg != None:
        MSG_FONT = pygame.font.Font(None, 70)
        text = MSG_FONT.render(extra_msg, True, RED)
        text_rect = text.get_rect(center=(WIDTH / 2, 50))
        screen.blit(text, text_rect)

    OPTIONS_FONT = pygame.font.Font(None, 70)
    for i, option in enumerate(menu_options):
        color = YELLOW if i == current_selection else WHITE
        text = OPTIONS_FONT.render(option, True, color)     # param 2: antialias: smoother edges
        text_rect = text.get_rect(center=(WIDTH / 2, 150 + i * 100))    # 150 offset
        screen.blit(text, text_rect)
    pygame.display.flip()

def main():
    LOG.info("Startup ...")
    global current_selection
    
    reload_menu()

    extra_msg = None
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                game_exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_DOWN:
                    if current_selection <= len(menu_options) - 2:   # one because of index, one for future move
                        current_selection += 1
                elif event.key == pygame.K_UP:
                    if current_selection >= 1:
                        current_selection -= 1
                elif event.key == pygame.K_RETURN:
                    if continued_game_option:
                        if current_selection == 0:
                            extra_msg = start_game(True)
                        elif current_selection == 1:
                            current_selection = 0
                            extra_msg = start_game(False)
                        elif current_selection == 2:
                            current_selection = 0
                            settings_menu()
                        elif current_selection == 3:
                            current_selection = 0
                            game_exit()
                    else:
                        if current_selection == 0:
                            extra_msg = start_game(True)
                        elif current_selection == 1:
                            current_selection = 0
                            settings_menu()
                        elif current_selection == 2:
                            current_selection = 0
                            game_exit()
        draw_menu(extra_msg)

if __name__ == "__main__":
    main()

# TODO: respawn time of ghosts can be abused to escape ghosts on a continued game
# TODO: bug: score saving not working
