import pygame
import sys
import json
import math
from threading import Thread
from time import sleep
import queue

# Initialize Pygame
pygame.init()

# Screen dimensions
WIDTH, HEIGHT = 800, 600
global screen
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Pac-Man")


# Colors
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

TILE_SIZE = 24
GHOST_SIZE = 12
COIN_SIZE = 4
COIN_DISPLAY_FONT = pygame.font.Font(None, 24)
COIN_RESPAWN_TIME = 30

WALL_SYMBOL = "#"
EMPTY_SYMBOL = " "
COIN_SYMBOL = "."
GHOST_SYMBOL = "!"

ghosts = []

screen_update_queue = queue.Queue()

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
        self.speed = 1
        self.radius = 12
        self.coins = 0

    def draw(self, screen):
        pygame.draw.circle(screen, PLAYER_COLOR, block_pos_to_pixel(self.x, self.y), self.radius)

    def move(self, dx, dy):
        self.x += dx * self.speed
        self.y += dy * self.speed
        print("debug:", self.x, self.y)

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

class Ghost:
    def __init__(self, x, y, player):
        self.x = x
        self.y = y
        self.speed = 1
        self.player = player

    def future_pos(self, dx, dy, x=None, y=None): # can use a custom starting point
        if x is None:
            x = self.x
        if y is None:
            y = self.y
        x = (dx * self.speed) + x
        y = (dy * self.speed) + y
        return x, y

    def auto_move(self):
        find_shortest_way(self, (self.x, self.y), (self.player.x, self.player.y))



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
    elif entity_on_block == COIN_SYMBOL:
        return (True, COIN_SYMBOL)

    return (True, False)


def update_screen(player):

    while True:
        debug = screen_update_queue.get()           # callbacks making sure this function isn't called twice at the same time
        #print("received update request")

        screen.fill(BLACK)
        draw_maze(screen, maze)

        player.draw(screen)

        display_coins(player.coins)

        if debug != None:
            debug()

        pygame.display.flip()   # update display

        screen_update_queue.task_done()

def regenerate_item(entity, pos, player, in_future=0):

    if in_future != 0:
        sleep(in_future)
    
    if player.x != pos[0] or player.y != pos[1]:  # prevents afk coin farm
        entity_on_block = maze[pos[1]-1][pos[0]-1]
        #print(f"'{entity_on_block}'")
        if entity_on_block == EMPTY_SYMBOL:  # prevents to spawn multiple entities on single block
            update_block(pos[0], pos[1], entity)
            
def update_block(x_block, y_block, updated_block):
    row = maze[y_block-1]
    new_row = row[:x_block-1] + updated_block + row[x_block:]
    maze[y_block-1] = new_row

    screen_update_queue.put(None)

def entity_collision_handler(player, entity):
    if entity == COIN_SYMBOL:
        player.add_coin()
        
        update_block(player.x, player.y, EMPTY_SYMBOL)

        thread = Thread(target=regenerate_item, args=(entity, (player.x, player.y), player, COIN_RESPAWN_TIME), daemon=True)
        thread.start()


def display_coins(count):
    text = COIN_DISPLAY_FONT.render(f'Coins: {count}', True, WHITE)
    text_rect = text.get_rect()
    text_rect.topright = (screen.get_width() - 40, 5)
    screen.blit(text, text_rect)


def ghost_handler(ghost):
    # TODO: auto movement
    while True:
        ghost.auto_move()

        sleep(3000)


def entity_generator(player):

    # init phase
    #ghost = Ghost(13, 12, player)
    ghost = Ghost(2, 2, player)
    ghosts.append(ghost)
    update_block(ghost.x, ghost.y, GHOST_SYMBOL)
    ghost_thread = Thread(target=ghost_handler, args=(ghost,))
    ghost_thread.start()

    # loop
    while True:
        pass

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
    #pygame.draw.circle(screen, GREEN, (x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2), COIN_SIZE)
    #screen_update_queue.put(None)
    screen_update_queue.put(lambda: pygame.draw.circle(screen, color, ((x-1) * TILE_SIZE + TILE_SIZE // 2, (y-1) * TILE_SIZE + TILE_SIZE // 2), COIN_SIZE))

    #sleep(0.001)

def pathfinding(entity, ending_pos, res, current_pos, old_pos, last_was_corner, after_corners, hits, steps=0):

    #if res.count(False) == 4:
    #    print("dead end")
    #    return

    if current_pos == ending_pos:
        print("hit, steps:", steps)
        hits.append(steps)

    if not res.count(False) > 1:
        last_was_corner = True
    else:
        last_was_corner = False

    if len(hits) > 0:
        if steps > min(hits):
            return hits

    original_after_corners = after_corners.copy()   # to get same effect like the steps counter has (resets automatically)

    for i in res:
        if i != False and i not in after_corners and i != old_pos:     # avoids unusable blocks, does loop detection, avoids going back
            new_res = check_all_directions(entity, i)
            #print(steps, i, current_pos, new_res)
            if last_was_corner:
                #print("corner add", after_corners)
                after_corners.append(i)
                #show_pathfinding(i[0], i[1], RED)
            else:
                #show_pathfinding(i[0], i[1], GREEN)
                pass
            hits = pathfinding(entity, ending_pos, new_res, i, current_pos, last_was_corner, after_corners, hits, steps+1)

    after_corners[:] = original_after_corners

    return hits

def find_shortest_way(entity, starting_pos, ending_pos):

    res = check_all_directions(entity, starting_pos)
    hits = pathfinding(entity, ending_pos, res, starting_pos, (0,0), False, [], [])
    if len(hits) == 0:
        print("no way found")
        return False
    
    shortest_way = min(hits)
    print(shortest_way)






def main():
    print("loading map")
    load_map()
    print("spawning player")
    player = Player(spawn[0], spawn[1])
    
    display_coins(player.coins)
    update_screen_thread = Thread(target=update_screen, args=(player,), daemon=True)
    update_screen_thread.start()

    entity_generator_thread = Thread(target=entity_generator, args=(player,), daemon=True)
    entity_generator_thread.start()

    screen_update_queue.put(None)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                dx, dy = 0, 0
                if event.key == pygame.K_DOWN:
                    dy = 1
                elif event.key == pygame.K_UP:
                    dy = -1
                elif event.key == pygame.K_LEFT:
                    dx = -1
                elif event.key == pygame.K_RIGHT:
                    dx = 1  

                if dx != 0 or dy != 0:

                    allowed, entity = check_collision(player, dx, dy)
                    if allowed:
                        player.move(dx, dy)
                        if entity:
                            entity_collision_handler(player, entity)

                        screen_update_queue.put(None)

                    

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
