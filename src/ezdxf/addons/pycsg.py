# License
# Copyright (c) 2011 Evan Wallace (http://madebyevan.com/), under the MIT license.
# Python port Copyright (c) 2012 Tim Knip (http://www.floorplanner.com), under the MIT license.
# Additions by Alex Pletzer (Pennsylvania State University)
# Adaptation as ezdxf add-on, Copyright (c) 2020, Manfred Moitzi, MIT License.
from typing import List
import math
import operator
from functools import reduce

__doc__ = """
Constructive Solid Geometry (CSG) is a modeling technique that uses Boolean
operations like union and intersection to combine 3D solids. This library
implements CSG operations on meshes elegantly and concisely using BSP trees,
and is meant to serve as an easily understandable implementation of the
algorithm. All edge cases involving overlapping coplanar polygons in both
solids are correctly handled.

Example usage::

    from csg.core import CSG
    
    cube = CSG.cube()
    sphere = CSG.sphere({'radius': 1.3})
    polygons = cube.subtract(sphere).to_polygons()

## Implementation Details

All CSG operations are implemented in terms of two functions, `clip_to()` and
`invert()`, which remove parts of a BSP tree inside another BSP tree and swap
solid and empty space, respectively. To find the union of `a` and `b`, we
want to remove everything in `a` inside `b` and everything in `b` inside `a`,
then combine polygons from `a` and `b` into one solid::

    a.clip_to(b)
    b.clip_to(a)
    a.build(b.all_polygons())

The only tricky part is handling overlapping coplanar polygons in both trees.
The code above keeps both copies, but we need to keep them in one tree and
remove them in the other tree. To remove them from `b` we can clip the
inverse of `b` against `a`. The code for union now looks like this::

    a.clip_to(b)
    b.clip_to(a)
    b.invert()
    b.clip_to(a)
    b.invert()
    a.build(b.all_polygons())

Subtraction and intersection naturally follow from set operations. If
union is `A | B`, subtraction is `A - B = ~(~A | B)` and intersection is
`A & B = ~(~A | ~B)` where `~` is the complement operator.

"""


# increase the max number of recursive calls
# sys.setrecursionlimit(10000)  # my default is 1000, increasing too much may cause a seg fault


class Vector:
    """ Immutable 3D vector. """
    __slots__ = ('_x', '_y', '_z')

    def __init__(self, *args):
        try:  # fast path Vector(x, y, z)
            self._x, self._y, self._z = args
        except ValueError:
            # slow path Vector([x, y, z]) and Vector(None)
            self._x, self._y, self._z = args[0] or (0., 0., 0.)

    @property
    def x(self) -> float:
        return self._x

    @property
    def y(self) -> float:
        return self._y

    @property
    def z(self) -> float:
        return self._z

    def copy(self) -> 'Vector':
        return Vector(self._x, self._y, self._z)

    def negated(self) -> 'Vector':
        """ Negated. """
        return Vector(-self._x, -self._y, -self._z)

    __neg__ = negated

    def plus(self, a: 'Vector') -> 'Vector':
        """ Add. """
        return Vector(self._x + a.x, self._y + a.y, self._z + a.z)

    __add__ = plus

    def minus(self, a: 'Vector') -> 'Vector':
        """ Subtract. """
        return Vector(self._x - a.x, self._y - a.y, self._z - a.z)

    __sub__ = minus

    def times(self, a: float) -> 'Vector':
        """ Multiply. """
        return Vector(self._x * a, self._y * a, self._z * a)

    __mul__ = times

    def divide(self, a: float) -> 'Vector':
        """ Divide. """
        return Vector(self._x / a, self._y / a, self._z / a)

    __truediv__ = divide

    def dot(self, a: 'Vector') -> float:
        """ Dot. """
        return self._x * a.x + self._y * a.y + self._z * a.z

    def lerp(self, a: 'Vector', t: float) -> 'Vector':
        """ Lerp: Linear interpolation from self to a """
        return self.plus(a.minus(self).times(t))

    def length(self) -> float:
        """ Length """
        return math.sqrt(self.dot(self))

    def unit(self) -> 'Vector':
        """ Normalize """
        return self.divide(self.length())

    def cross(self, a) -> 'Vector':
        """ Cross Product """
        return Vector(
            self._y * a.z - self._z * a.y,
            self._z * a.x - self._x * a.z,
            self._x * a.y - self._y * a.x,
        )

    def round(self, ndigits: int = 6):
        return Vector(
            round(self._x, ndigits=ndigits),
            round(self._y, ndigits=ndigits),
            round(self._z, ndigits=ndigits),
        )

    def __getitem__(self, key: int) -> float:
        return (self._x, self._y, self._z)[key]

    def __repr__(self) -> str:
        return 'Vector(%.2f, %.2f, %0.2f)' % (self._x, self._y, self._z)


class Vertex:
    """
    Class Vertex

    Represents a vertex of a polygon. Use your own vertex class instead of this
    one to provide additional features like texture coordinates and vertex
    colors. Custom vertex classes need to provide a `pos` property and `clone()`,
    `flip()`, and `interpolate()` methods that behave analogous to the ones
    defined by `Vertex`. This class provides `normal` so convenience
    functions like `CSG.sphere()` can return a smooth vertex normal, but `normal`
    is not used anywhere else.
    """
    __slots__ = ('pos', 'normal')

    def __init__(self, pos: Vector, normal: Vector = None):
        self.pos = Vector(pos)
        self.normal = Vector(normal)

    def clone(self) -> 'Vertex':
        return Vertex(self.pos, self.normal)

    def flip(self):
        """
        Invert all orientation-specific data (e.g. vertex normal). Called when the
        orientation of a polygon is flipped.
        """
        self.normal = -self.normal

    def interpolate(self, other, t) -> 'Vertex':
        """
        Create a new vertex between this vertex and `other` by linearly
        interpolating all properties using a parameter of `t`. Subclasses should
        override this to interpolate additional properties.
        """
        return Vertex(self.pos.lerp(other.pos, t),
                      self.normal.lerp(other.normal, t))

    def __repr__(self) -> str:
        return repr(self.pos)


COPLANAR = 0  # all the vertices are within EPSILON distance from plane
FRONT = 1  # all the vertices are in front of the plane
BACK = 2  # all the vertices are at the back of the plane
SPANNING = 3  # some vertices are in front, some in the back


class Plane:
    """
    class Plane

    Represents a plane in 3D space.
    """

    """
    `Plane.EPSILON` is the tolerance used by `split_polygon()` to decide if a
    point is on the plane.
    """
    EPSILON = 1.e-5
    __slots__ = ('normal', 'w')

    def __init__(self, normal: Vector, w: float):
        self.normal = normal
        # w is the (perpendicular) distance of the plane from (0, 0, 0)
        self.w = w

    @classmethod
    def from_points(cls, a, b, c) -> 'Plane':
        n = b.minus(a).cross(c.minus(a)).unit()
        return Plane(n, n.dot(a))

    def clone(self) -> 'Plane':
        return Plane(self.normal, self.w)

    def flip(self):
        self.normal = -self.normal
        self.w = -self.w

    def __repr__(self) -> str:
        return f'Plane({self.normal}, {self.w})'

    def split_polygon(self, polygon: 'Polygon',
                      coplanar_front: List['Polygon'], coplanar_back: List['Polygon'],
                      front: List['Polygon'], back: List['Polygon']):
        """
        Split `polygon` by this plane if needed, then put the polygon or polygon
        fragments in the appropriate lists. Coplanar polygons go into either
        `coplanarFront` or `coplanarBack` depending on their orientation with
        respect to this plane. Polygons in front or in back of this plane go into
        either `front` or `back`
        """

        # Classify each point as well as the entire polygon into one of the above
        # four classes.
        polygon_type = 0
        vertex_locations = []

        num_vertices = len(polygon.vertices)
        for i in range(num_vertices):
            t = self.normal.dot(polygon.vertices[i].pos) - self.w
            loc = -1
            if t < -Plane.EPSILON:
                loc = BACK
            elif t > Plane.EPSILON:
                loc = FRONT
            else:
                loc = COPLANAR
            polygon_type |= loc
            vertex_locations.append(loc)

        # Put the polygon in the correct list, splitting it when necessary.
        if polygon_type == COPLANAR:
            normal_dot_plane_normal = self.normal.dot(polygon.plane.normal)
            if normal_dot_plane_normal > 0:
                coplanar_front.append(polygon)
            else:
                coplanar_back.append(polygon)
        elif polygon_type == FRONT:
            front.append(polygon)
        elif polygon_type == BACK:
            back.append(polygon)
        elif polygon_type == SPANNING:
            f = []
            b = []
            for i in range(num_vertices):
                j = (i + 1) % num_vertices
                ti = vertex_locations[i]
                tj = vertex_locations[j]
                vi = polygon.vertices[i]
                vj = polygon.vertices[j]
                if ti != BACK:
                    f.append(vi)
                if ti != FRONT:
                    if ti != BACK:
                        b.append(vi.clone())
                    else:
                        b.append(vi)
                if (ti | tj) == SPANNING:
                    # interpolation weight at the intersection point
                    t = (self.w - self.normal.dot(vi.pos)) / self.normal.dot(vj.pos.minus(vi.pos))
                    # intersection point on the plane
                    v = vi.interpolate(vj, t)
                    f.append(v)
                    b.append(v.clone())
            if len(f) >= 3:
                front.append(Polygon(f, polygon.shared))
            if len(b) >= 3:
                back.append(Polygon(b, polygon.shared))


class Polygon:
    """
    class Polygon

    Represents a convex polygon. The vertices used to initialize a polygon must
    be coplanar and form a convex loop. They do not have to be `Vertex`
    instances but they must behave similarly (duck typing can be used for
    customization).

    Each convex polygon has a `shared` property, which is shared between all
    polygons that are clones of each other or were split from the same polygon.
    This can be used to define per-polygon properties (such as surface color).
    """

    def __init__(self, vertices: List[Vertex], shared=None):
        self.vertices = vertices
        self.shared = shared
        self.plane = Plane.from_points(vertices[0].pos, vertices[1].pos, vertices[2].pos)

    def clone(self):
        return Polygon(list(self.vertices), self.shared)

    def flip(self):
        self.vertices.reverse()
        map(lambda v: v.flip(), self.vertices)
        self.plane.flip()

    def __repr__(self):
        return reduce(lambda x, y: x + y, ['Polygon(['] + [repr(v) + ', ' for v in self.vertices] + ['])'], '')


class BSPNode:
    """
    class BSPNode

    Holds a node in a BSP tree. A BSP tree is built from a collection of polygons
    by picking a polygon to split along. That polygon (and all other coplanar
    polygons) are added directly to that node and the other polygons are added to
    the front and/or back subtrees. This is not a leafy BSP tree since there is
    no distinction between internal and leaf nodes.
    """

    def __init__(self, polygons: List[Polygon] = None):
        self.plane = None  # type: Plane
        self.front = None  # type: BSPNode
        self.back = None  # type: BSPNode
        self.polygons = []  # type: List[Polygon]
        if polygons:
            self.build(polygons)

    def clone(self):
        node = BSPNode()
        if self.plane:
            node.plane = self.plane.clone()
        if self.front:
            node.front = self.front.clone()
        if self.back:
            node.back = self.back.clone()
        node.polygons = list(map(lambda p: p.clone(), self.polygons))
        return node

    def invert(self):
        """
        Convert solid space to empty space and empty space to solid space.
        """
        for poly in self.polygons:
            poly.flip()
        self.plane.flip()
        if self.front:
            self.front.invert()
        if self.back:
            self.back.invert()
        temp = self.front
        self.front = self.back
        self.back = temp

    def clip_polygons(self, polygons: List['Polygon']):
        """
        Recursively remove all polygons in `polygons` that are inside this BSP
        tree.
        """
        if not self.plane:
            return polygons[:]

        front = []
        back = []
        for poly in polygons:
            self.plane.split_polygon(poly, front, back, front, back)

        if self.front:
            front = self.front.clip_polygons(front)

        if self.back:
            back = self.back.clip_polygons(back)
        else:
            back = []

        front.extend(back)
        return front

    def clip_to(self, bsp):
        """
        Remove all polygons in this BSP tree that are inside the other BSP tree
        `bsp`.
        """
        self.polygons = bsp.clip_polygons(self.polygons)
        if self.front:
            self.front.clip_to(bsp)
        if self.back:
            self.back.clip_to(bsp)

    def all_polygons(self):
        """
        Return a list of all polygons in this BSP tree.
        """
        polygons = self.polygons[:]
        if self.front:
            polygons.extend(self.front.all_polygons())
        if self.back:
            polygons.extend(self.back.all_polygons())
        return polygons

    def build(self, polygons: List[Polygon]):
        """
        Build a BSP tree out of `polygons`. When called on an existing tree, the
        new polygons are filtered down to the bottom of the tree and become new
        nodes there. Each set of polygons is partitioned using the first polygon
        (no heuristic is used to pick a good split).
        """
        if len(polygons) == 0:
            return
        if not self.plane:
            self.plane = polygons[0].plane.clone()
        # add polygon to this node
        self.polygons.append(polygons[0])
        front = []
        back = []
        # split all other polygons using the first polygon's plane
        for poly in polygons[1:]:
            # coplanar front and back polygons go into self.polygons
            self.plane.split_polygon(poly, self.polygons, self.polygons,
                                     front, back)
        # recursively build the BSP tree
        if len(front) > 0:
            if not self.front:
                self.front = BSPNode()
            self.front.build(front)
        if len(back) > 0:
            if not self.back:
                self.back = BSPNode()
            self.back.build(back)


class CSG:
    """
    Constructive Solid Geometry (CSG) is a modeling technique that uses Boolean
    operations like union and intersection to combine 3D solids. This library
    implements CSG operations on meshes elegantly and concisely using BSP trees,
    and is meant to serve as an easily understandable implementation of the
    algorithm. All edge cases involving overlapping coplanar polygons in both
    solids are correctly handled.
    
    """

    def __init__(self):
        self.polygons = []

    @classmethod
    def from_polygons(cls, polygons):
        csg = CSG()
        csg.polygons = polygons
        return csg

    def clone(self):
        csg = CSG()
        csg.polygons = list(map(lambda p: p.clone(), self.polygons))
        return csg

    def to_polygons(self):
        return self.polygons

    def refine(self):
        """
        Return a refined CSG. To each polygon, a middle point is added to each edge and to the center 
        of the polygon
        """
        new_csg = CSG()
        for poly in self.polygons:

            verts = poly.vertices
            num_verts = len(verts)

            if num_verts == 0:
                continue

            mid_pos = reduce(operator.add, [v.pos for v in verts]) / float(num_verts)
            mid_normal = None
            if verts[0].normal is not None:
                mid_normal = poly.plane.normal
            mid_vert = Vertex(mid_pos, mid_normal)

            new_verts = verts + \
                        [verts[i].interpolate(verts[(i + 1) % num_verts], 0.5) for i in range(num_verts)] + \
                        [mid_vert]

            i = 0
            vs = [new_verts[i], new_verts[i + num_verts], new_verts[2 * num_verts], new_verts[2 * num_verts - 1]]
            new_poly = Polygon(vs, poly.shared)
            new_poly.shared = poly.shared
            new_poly.plane = poly.plane
            new_csg.polygons.append(new_poly)

            for i in range(1, num_verts):
                vs = [new_verts[i], new_verts[num_verts + i], new_verts[2 * num_verts], new_verts[num_verts + i - 1]]
                new_poly = Polygon(vs, poly.shared)
                new_csg.polygons.append(new_poly)

        return new_csg

    def to_vertices_and_polygons(self):
        """
        Return list of vertices, polygons (cells), and the total
        number of vertex indices in the polygon connectivity list
        (count).
        """
        offset = 1.234567890
        verts = []
        polys = []
        vertex_index_map = {}
        count = 0
        for poly in self.polygons:
            verts = poly.vertices
            cell = []
            for v in poly.vertices:
                p = v.pos
                # use string key to remove degeneracy associated
                # very close points. The format %.10e ensures that
                # points differing in the 11 digits and higher are 
                # treated as the same. For instance 1.2e-10 and 
                # 1.3e-10 are essentially the same.
                vKey = '%.10e,%.10e,%.10e' % (p[0] + offset,
                                              p[1] + offset,
                                              p[2] + offset)
                if vKey not in vertex_index_map:
                    vertex_index_map[vKey] = len(vertex_index_map)
                index = vertex_index_map[vKey]
                cell.append(index)
                count += 1
            polys.append(cell)
        # sort by index
        sorted_vertex_index = sorted(vertex_index_map.items(),
                                     key=operator.itemgetter(1))
        verts = []
        for v, i in sorted_vertex_index:
            p = []
            for c in v.split(','):
                p.append(float(c) - offset)
            verts.append(tuple(p))
        return verts, polys, count

    def save_VTK(self, filename):
        """ Save polygons in VTK file. """
        with open(filename, 'w') as f:
            f.write('# vtk DataFile Version 3.0\n')
            f.write('pycsg output\n')
            f.write('ASCII\n')
            f.write('DATASET POLYDATA\n')

            verts, cells, count = self.to_vertices_and_polygons()

            f.write('POINTS {0} float\n'.format(len(verts)))
            for v in verts:
                f.write('{0} {1} {2}\n'.format(v[0], v[1], v[2]))
            numCells = len(cells)
            f.write('POLYGONS {0} {1}\n'.format(numCells, count + numCells))
            for cell in cells:
                f.write('{0} '.format(len(cell)))
                for index in cell:
                    f.write('{0} '.format(index))
                f.write('\n')

    def union(self, csg):
        """
        Return a new CSG solid representing space in either this solid or in the
        solid `csg`. Neither this solid nor the solid `csg` are modified.::
        
            A.union(B)
        
            +-------+            +-------+
            |       |            |       |
            |   A   |            |       |
            |    +--+----+   =   |       +----+
            +----+--+    |       +----+       |
                 |   B   |            |       |
                 |       |            |       |
                 +-------+            +-------+
        """
        a = BSPNode(self.clone().polygons)
        b = BSPNode(csg.clone().polygons)
        a.clip_to(b)
        b.clip_to(a)
        b.invert()
        b.clip_to(a)
        b.invert()
        a.build(b.all_polygons());
        return CSG.from_polygons(a.all_polygons())

    def __add__(self, csg):
        return self.union(csg)

    def subtract(self, csg):
        """
        Return a new CSG solid representing space in this solid but not in the
        solid `csg`. Neither this solid nor the solid `csg` are modified.::
        
            A.subtract(B)
        
            +-------+            +-------+
            |       |            |       |
            |   A   |            |       |
            |    +--+----+   =   |    +--+
            +----+--+    |       +----+
                 |   B   |
                 |       |
                 +-------+
        """
        a = BSPNode(self.clone().polygons)
        b = BSPNode(csg.clone().polygons)
        a.invert()
        a.clip_to(b)
        b.clip_to(a)
        b.invert()
        b.clip_to(a)
        b.invert()
        a.build(b.all_polygons())
        a.invert()
        return CSG.from_polygons(a.all_polygons())

    def __sub__(self, csg):
        return self.subtract(csg)

    def intersect(self, csg):
        """
        Return a new CSG solid representing space both this solid and in the
        solid `csg`. Neither this solid nor the solid `csg` are modified.::
        
            A.intersect(B)
        
            +-------+
            |       |
            |   A   |
            |    +--+----+   =   +--+
            +----+--+    |       +--+
                 |   B   |
                 |       |
                 +-------+
        """
        a = BSPNode(self.clone().polygons)
        b = BSPNode(csg.clone().polygons)
        a.invert()
        b.clip_to(a)
        b.invert()
        a.clip_to(b)
        b.clip_to(a)
        a.build(b.all_polygons())
        a.invert()
        return CSG.from_polygons(a.all_polygons())

    def __mul__(self, csg):
        return self.intersect(csg)

    def inverse(self):
        """
        Return a new CSG solid with solid and empty space switched. This solid is
        not modified.
        """
        csg = self.clone()
        map(lambda p: p.flip(), csg.polygons)
        return csg

    @classmethod
    def cube(cls, center=(0, 0, 0), radius=(1, 1, 1)):
        """
        Construct an axis-aligned solid cuboid. Optional parameters are `center` and
        `radius`, which default to `[0, 0, 0]` and `[1, 1, 1]`. The radius can be
        specified using a single number or a list of three numbers, one for each axis.
        
        Example code::
        
            cube = CSG.cube(
              center=[0, 0, 0],
              radius=1
            )
        """
        c = Vector(0, 0, 0)
        r = [1, 1, 1]
        if isinstance(center, (list, tuple)):
            c = Vector(center)
        if isinstance(radius, (list, tuple)):
            r = radius
        else:
            r = (radius, radius, radius)

        polygons = list(map(
            lambda v: Polygon(
                list(map(lambda i:
                         Vertex(
                             Vector(
                                 c.x + r[0] * (2 * bool(i & 1) - 1),
                                 c.y + r[1] * (2 * bool(i & 2) - 1),
                                 c.z + r[2] * (2 * bool(i & 4) - 1)
                             ),
                             None
                         ), v[0]))),
            [
                [[0, 4, 6, 2], [-1, 0, 0]],
                [[1, 3, 7, 5], [+1, 0, 0]],
                [[0, 1, 5, 4], [0, -1, 0]],
                [[2, 6, 7, 3], [0, +1, 0]],
                [[0, 2, 3, 1], [0, 0, -1]],
                [[4, 5, 7, 6], [0, 0, +1]]
            ]))
        return CSG.from_polygons(polygons)

    @classmethod
    def sphere(cls, **kwargs):
        """ Returns a sphere.
            
            Kwargs:
                center (list): Center of sphere, default [0, 0, 0].
                
                radius (float): Radius of sphere, default 1.0.
                
                slices (int): Number of slices, default 16.
                
                stacks (int): Number of stacks, default 8.
        """
        center = kwargs.get('center', [0.0, 0.0, 0.0])
        if isinstance(center, float):
            center = [center, center, center]
        c = Vector(center)
        r = kwargs.get('radius', 1.0)
        if isinstance(r, list) and len(r) > 2:
            r = r[0]
        slices = kwargs.get('slices', 16)
        stacks = kwargs.get('stacks', 8)
        polygons = []

        def append_vertex(vertices, theta, phi):
            d = Vector(
                math.cos(theta) * math.sin(phi),
                math.cos(phi),
                math.sin(theta) * math.sin(phi))
            vertices.append(Vertex(c.plus(d.times(r)), d))

        dTheta = math.pi * 2.0 / float(slices)
        dPhi = math.pi / float(stacks)

        j0 = 0
        j1 = j0 + 1
        for i0 in range(0, slices):
            i1 = i0 + 1
            #  +--+
            #  | /
            #  |/
            #  +
            vertices = []
            append_vertex(vertices, i0 * dTheta, j0 * dPhi)
            append_vertex(vertices, i1 * dTheta, j1 * dPhi)
            append_vertex(vertices, i0 * dTheta, j1 * dPhi)
            polygons.append(Polygon(vertices))

        j0 = stacks - 1
        j1 = j0 + 1
        for i0 in range(0, slices):
            i1 = i0 + 1
            #  +
            #  |\
            #  | \
            #  +--+
            vertices = []
            append_vertex(vertices, i0 * dTheta, j0 * dPhi)
            append_vertex(vertices, i1 * dTheta, j0 * dPhi)
            append_vertex(vertices, i0 * dTheta, j1 * dPhi)
            polygons.append(Polygon(vertices))

        for j0 in range(1, stacks - 1):
            j1 = j0 + 0.5
            j2 = j0 + 1
            for i0 in range(0, slices):
                i1 = i0 + 0.5
                i2 = i0 + 1
                #  +---+
                #  |\ /|
                #  | x |
                #  |/ \|
                #  +---+
                verticesN = []
                append_vertex(verticesN, i1 * dTheta, j1 * dPhi)
                append_vertex(verticesN, i2 * dTheta, j2 * dPhi)
                append_vertex(verticesN, i0 * dTheta, j2 * dPhi)
                polygons.append(Polygon(verticesN))
                verticesS = []
                append_vertex(verticesS, i1 * dTheta, j1 * dPhi)
                append_vertex(verticesS, i0 * dTheta, j0 * dPhi)
                append_vertex(verticesS, i2 * dTheta, j0 * dPhi)
                polygons.append(Polygon(verticesS))
                verticesW = []
                append_vertex(verticesW, i1 * dTheta, j1 * dPhi)
                append_vertex(verticesW, i0 * dTheta, j2 * dPhi)
                append_vertex(verticesW, i0 * dTheta, j0 * dPhi)
                polygons.append(Polygon(verticesW))
                verticesE = []
                append_vertex(verticesE, i1 * dTheta, j1 * dPhi)
                append_vertex(verticesE, i2 * dTheta, j0 * dPhi)
                append_vertex(verticesE, i2 * dTheta, j2 * dPhi)
                polygons.append(Polygon(verticesE))

        return CSG.from_polygons(polygons)

    @classmethod
    def cylinder(cls, **kwargs):
        """ Returns a cylinder.
            
            Kwargs:
                start (list): Start of cylinder, default [0, -1, 0].
                
                end (list): End of cylinder, default [0, 1, 0].
                
                radius (float): Radius of cylinder, default 1.0.
                
                slices (int): Number of slices, default 16.
        """
        s = kwargs.get('start', Vector(0.0, -1.0, 0.0))
        e = kwargs.get('end', Vector(0.0, 1.0, 0.0))
        if isinstance(s, list):
            s = Vector(*s)
        if isinstance(e, list):
            e = Vector(*e)
        r = kwargs.get('radius', 1.0)
        slices = kwargs.get('slices', 16)
        ray = e.minus(s)

        z_axis = ray.unit()
        is_y = (math.fabs(z_axis.y) > 0.5)
        x_axis = Vector(float(is_y), float(not is_y), 0).cross(z_axis).unit()
        y_axis = x_axis.cross(z_axis).unit()
        start = Vertex(s, z_axis.negated())
        end = Vertex(e, z_axis.unit())
        polygons = []

        def point(stack, angle, normalBlend):
            out = x_axis.times(math.cos(angle)).plus(
                y_axis.times(math.sin(angle)))
            pos = s.plus(ray.times(stack)).plus(out.times(r))
            normal = out.times(1.0 - math.fabs(normalBlend)).plus(
                z_axis.times(normalBlend))
            return Vertex(pos, normal)

        dt = math.pi * 2.0 / float(slices)
        for i in range(0, slices):
            t0 = i * dt
            i1 = (i + 1) % slices
            t1 = i1 * dt
            polygons.append(Polygon([start.clone(),
                                     point(0., t0, -1.),
                                     point(0., t1, -1.)]))
            polygons.append(Polygon([point(0., t1, 0.),
                                     point(0., t0, 0.),
                                     point(1., t0, 0.),
                                     point(1., t1, 0.)]))
            polygons.append(Polygon([end.clone(),
                                     point(1., t1, 1.),
                                     point(1., t0, 1.)]))

        return CSG.from_polygons(polygons)

    @classmethod
    def cone(cls, **kwargs):
        """ Returns a cone.
            
            Kwargs:
                start (list): Start of cone, default [0, -1, 0].
                
                end (list): End of cone, default [0, 1, 0].
                
                radius (float): Maximum radius of cone at start, default 1.0.
                
                slices (int): Number of slices, default 16.
        """
        s = kwargs.get('start', Vector(0.0, -1.0, 0.0))
        e = kwargs.get('end', Vector(0.0, 1.0, 0.0))
        if isinstance(s, list):
            s = Vector(*s)
        if isinstance(e, list):
            e = Vector(*e)
        r = kwargs.get('radius', 1.0)
        slices = kwargs.get('slices', 16)
        ray = e.minus(s)

        z_axis = ray.unit()
        is_y = (math.fabs(z_axis._y) > 0.5)
        x_axis = Vector(float(is_y), float(not is_y), 0).cross(z_axis).unit()
        y_axis = x_axis.cross(z_axis).unit()
        start_normal = z_axis.negated()
        start = Vertex(s, start_normal)
        polygons = []

        taper_angle = math.atan2(r, ray.length())
        sin_taper_angle = math.sin(taper_angle)
        cos_taper_angle = math.cos(taper_angle)

        def point(angle):
            # radial direction pointing out
            out = x_axis.times(math.cos(angle)).plus(
                y_axis.times(math.sin(angle)))
            pos = s.plus(out.times(r))
            # normal taking into account the tapering of the cone
            normal = out.times(cos_taper_angle).plus(z_axis.times(sin_taper_angle))
            return pos, normal

        dt = math.pi * 2.0 / float(slices)
        for i in range(0, slices):
            t0 = i * dt
            i1 = (i + 1) % slices
            t1 = i1 * dt
            # coordinates and associated normal pointing outwards of the cone's
            # side
            p0, n0 = point(t0)
            p1, n1 = point(t1)
            # average normal for the tip
            n_avg = n0.plus(n1).times(0.5)
            # polygon on the low side (disk sector)
            poly_start = Polygon([start.clone(),
                                  Vertex(p0, start_normal),
                                  Vertex(p1, start_normal)])
            polygons.append(poly_start)
            # polygon extending from the low side to the tip
            poly_side = Polygon([Vertex(p0, n0), Vertex(e, n_avg), Vertex(p1, n1)])
            polygons.append(poly_side)

        return CSG.from_polygons(polygons)