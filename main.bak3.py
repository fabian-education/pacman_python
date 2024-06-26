import pygame
import sys
import json
import math
from threading import Thread
from time import sleep
import queue
import logging
import datetime
import random

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

WIDTH, HEIGHT = 800, 600
global screen
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Pac-Man")


BLACK = (0, 0, 0)
YELLOW = (255, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)

global maze
global spawn
global size
maze = []
spawn = []
size = []

WALL_COLOR = BLUE
COIN_COLOR = WHITE
PLAYER_COLOR = YELLOW
GHOST_COLOR = RED

scaling_factor = 1
DEFAULT_SIZE = 24
TILE_SIZE = DEFAULT_SIZE * scaling_factor # in pixel, can be modified for scaling
GHOST_SIZE = TILE_SIZE / 2
COIN_SIZE = TILE_SIZE / 6
COIN_DISPLAY_FONT = pygame.font.Font(None, int(DEFAULT_SIZE * scaling_factor))  # scaling the font seems to work with scaling >= 0.5
COIN_RESPAWN_TIME = 30

controlling = "both"    # "both": push or hold; "push": push only
lvl = "hard"            # "easy", "medium" or "hard"

WALL_SYMBOL = "#"
EMPTY_SYMBOL = " "
COIN_SYMBOL = "."
GHOST_SYMBOL = "!"

CAN_MOVE_EVENT = pygame.USEREVENT + 1

ghosts = {}

screen_update_queue = queue.Queue()
maze_update_queue = queue.Queue()

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
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.speed = 1  # in blocks
        self.move_cooldown = 0.5   # in sec
        self.can_move = True
        self.is_alive = True
        self.radius = TILE_SIZE / 2
        self.coins = 0

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

    def add_coin(self):
        self.coins += 1

    def kill(self):     # TODO: implement ending
        LOG.info("Player killed")
        self.is_alive = False
        screen_update_queue.put(None)

class Ghost:
    def __init__(self, id, x, y, player):
        self.id = id
        self.x = x
        self.y = y
        self.speed = 1
        self.cooldown = 1       # in sec
        self.player = player
        self.spawn_lock_time = 5    # in sec

        ghosts[id] = self

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
                dx = 1
            elif next_step == 1:   # W
                dx = -1
            elif next_step == 2:   # S
                dy = 1
            elif next_step == 3:   # N
                dy = -1

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
        if len(path) > 0:
            return path[0]
        return False



def load_map(lvl = "easy"):
    global maze
    global spawn
    global size
    with open('maps.json', 'r') as f:
        d = json.load(f)
        #print(d)
        maze = d["maps"][lvl]["data"]
        spawn = d["maps"][lvl]["spawn"] # TODO: implement options like random, centered

    size = block_pos_to_pixel(len(maze[0]), len(maze), center_block=False)
    resize_window(size[0], size[1])

def draw_maze(screen, maze):
    maze_update_queue.join()    # waits for maze updates to be completed
    for y, row in enumerate(maze):
        for x, char in enumerate(row):
            if char == WALL_SYMBOL:
                pygame.draw.rect(screen, WALL_COLOR, (x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE))
            elif char == COIN_SYMBOL:
                pygame.draw.circle(screen, COIN_COLOR, (x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2), COIN_SIZE)
            elif char == GHOST_SYMBOL:  # TODO: handle existing ghosts
                pygame.draw.circle(screen, GHOST_COLOR, (x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2), GHOST_SIZE)
            
                leg_width = GHOST_SIZE // 2
                leg_height = (GHOST_SIZE // 2) + (GHOST_SIZE / 2)
                left_leg_rect = pygame.Rect(x * TILE_SIZE + TILE_SIZE // 2 - GHOST_SIZE, y * TILE_SIZE + TILE_SIZE // 2, leg_width, leg_height)
                right_leg_rect = pygame.Rect(x * TILE_SIZE + TILE_SIZE // 2 + GHOST_SIZE - leg_width, y * TILE_SIZE + TILE_SIZE // 2, leg_width, leg_height)
                pygame.draw.rect(screen, GHOST_COLOR, left_leg_rect)
                pygame.draw.rect(screen, GHOST_COLOR, right_leg_rect)

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

    entity_on_block = maze[future_pos[1]-1][future_pos[0]-1]

    if entity_on_block == WALL_SYMBOL:
        return (False, False)
    elif entity_on_block == EMPTY_SYMBOL:
        return (True, False)
    
    return (True, entity_on_block)


def update_screen(player):

    while True:
        debug = screen_update_queue.get()           # callbacks making sure this function isn't called twice at the same time

        screen.fill(BLACK)
        draw_maze(screen, maze)

        if player.is_alive:
            player.draw(screen)

        display_coins(player.coins)

        if debug != None:
            debug()

        pygame.display.flip()   # update display

        screen_update_queue.task_done()

def regenerate_item(entity, pos, player, in_future=0):      # some bug that the coins don't respawn/on wrong pos?

    while True:

        if in_future != 0:
            sleep(in_future)
        
        if player.x != pos[0] or player.y != pos[1]:        # prevents afk coin farm
            if get_block(pos[0], pos[1]) == EMPTY_SYMBOL:   # prevents to spawn multiple entities on single block
                update_block(pos[0], pos[1], entity)
                if entity == COIN_SYMBOL:
                    COIN_LOG.debug(f"respawned coin at {pos[0]},{pos[1]}")
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

        player.add_coin()
        COIN_LOG.debug(f"collected coin at {pos_x},{pos_y}")

        thread = Thread(target=regenerate_item, args=(entity, (pos_x, pos_y), player, COIN_RESPAWN_TIME), daemon=True)
        thread.start()

    elif entity == GHOST_SYMBOL:
        player.kill()


def display_coins(count):
    text = COIN_DISPLAY_FONT.render(f'Coins: {count}', True, WHITE)
    text_rect = text.get_rect()
    text_rect.topright = (screen.get_width() - 40, 5)
    screen.blit(text, text_rect)


def ghost_handler(ghost, init_old_symbol):

    old_symbol = init_old_symbol
    old_x, old_y = ghost.x, ghost.y

    while True:
        
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
                old_x = current_x
                old_y = current_y

        else:
            LOG.error(f"old_symbol not found: {old_symbol}")

        sleep(0.5)      # TODO: decrease based on time

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

def summon_ghost(target_player):
    ghost_id = get_next_ghost_id()
    LOG.info(f"spawning ghost{ghost_id}")
    spawn = get_random_spawn_block([EMPTY_SYMBOL, COIN_SYMBOL], target_player)       # TODO: condition to spawn ghosts with minimum distance to player
    if spawn != False:
        ghost = Ghost(ghost_id, spawn[0], spawn[1], target_player)

        old_symbol = get_block(ghost.x, ghost.y)
        update_block(ghost.x, ghost.y, GHOST_SYMBOL)
        ghost_thread = Thread(target=ghost_handler, args=(ghost, old_symbol))
        sleep(ghost.spawn_lock_time)
        ghost_thread.start()
        

def entity_generator(player):

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
            sleep(60)
            summon_ghost(player)
        
        if lvl == "medium":
            sleep(45)
            summon_ghost(player)

        if lvl == "hard":
            sleep(30)
            summon_ghost(player)

def check_all_directions(entity, pos):
    result = []
    dx, dy = 0, 0
    for _ in range(4):  # directions
        if dx == 0 and dy == 0:     # O
            dx = 1
        elif dx == 1 and dy == 0:   # W
            dx = -1
        elif dx == -1 and dy == 0:  # S
            dx = 0
            dy = 1
        elif dx == 0 and dy == 1:   # N
            dy = -1

        if check_collision(entity, dx, dy, pos[0], pos[1])[0]:
            result.append(entity.future_pos(dx, dy, x=pos[0], y=pos[1]))
        else:
            result.append(False)

    return result

def show_pathfinding(x, y, color):     # debug function
    screen_update_queue.put(lambda: pygame.draw.circle(screen, color, ((x-1) * TILE_SIZE + TILE_SIZE // 2, (y-1) * TILE_SIZE + TILE_SIZE // 2), COIN_SIZE))
    #sleep(0.001)

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

def main():
    LOG.info("Startup ...")
    LOG.info("loading map")
    load_map(lvl)
    LOG.info("spawning player")
    player = Player(spawn[0], spawn[1])
    
    display_coins(player.coins)
    update_screen_thread = Thread(target=update_screen, args=(player,), daemon=True)
    update_screen_thread.start()

    update_maze_thread = Thread(target=update_maze_block, daemon=True)
    update_maze_thread.start()

    entity_generator_thread = Thread(target=entity_generator, args=(player,), daemon=True)
    entity_generator_thread.start()

    screen_update_queue.put(None)

    running = True
    player.can_move = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

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



                    
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
