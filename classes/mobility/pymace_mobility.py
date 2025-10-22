import random

__author__ = "Bruno Chianca Ferreira"
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Bruno Chianca Ferreira"
__email__ = "brunobcf@gmail.com"

class Attraction():
  """_summary_
  """
  def __init__(self, pos, maxvel, minvel) -> None:
    """_summary_

    Args:
        pos (_type_): _description_
        maxvel (_type_): _description_
        minvel (_type_): _description_
    """
    self.maxvel = maxvel
    self.minvel = minvel
    self.velx = random.uniform(self.minvel, self.maxvel)
    self.vely = random.uniform(self.minvel, self.maxvel)
    self.velz = random.uniform(self.minvel, self.maxvel)
    self.attraction = [pos[0], pos[1], pos[2]]
    self.attraction_base = [pos[0], pos[1], pos[2]]
    self.pos = [pos[0], pos[1], pos[2]]
    self.shifttimer = 2
    self.shiftcount = 0
    self.shiftdelta = 20

  def step(self):
    """_summary_
    """
    pass

  def set_attraction(self, pos):
    """_summary_

    Args:
        pos (_type_): _description_
    """
    self.attraction = [pos[0], pos[1], pos[2]]

  def step(self):
    """_summary_

    Returns:
        _type_: _description_
    """
    dx = ((self.attraction[0] - self.pos[0]) / self.velx)
    dy = ((self.attraction[1] - self.pos[1]) / self.vely)
    dz = ((self.attraction[2] - self.pos[2]) / self.velz)

    self.pos[0] = self.pos[0] + dx
    self.pos[1] = self.pos[1] + dy
    self.pos[2] = self.pos[2] + dz

    self.shiftcount += 0.1

    if self.shiftcount >= self.shifttimer:
      self.set_attraction([self.attraction_base[0] + random.randint(0,self.shiftdelta),self.attraction_base[1] + random.randint(0,self.shiftdelta),self.attraction_base[2] + random.randint(0,self.shiftdelta)])
      self.velx = random.uniform(self.minvel, self.maxvel)
      self.vely = random.uniform(self.minvel, self.maxvel)
      self.velz = random.uniform(self.minvel, self.maxvel)
    
    return self.pos
