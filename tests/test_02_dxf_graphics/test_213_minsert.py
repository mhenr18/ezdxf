# Copyright (c) 2020, Manfred Moitzi
# License: MIT License
import pytest
import ezdxf
from ezdxf.entities import Insert


def test_mcount_property():
    insert = Insert.new()
    insert.grid(size=(2, 2), spacing=(10, 10))
    assert insert.mcount == 4

    insert.grid(size=(2, 2), spacing=(10, 0))
    assert insert.mcount == 2

    insert.grid(size=(2, 2), spacing=(0, 10))
    assert insert.mcount == 2

    insert.grid(size=(2, 2), spacing=(0, 0))
    assert insert.mcount == 1


class TestSimpleBlock:
    # without ATTRIB, no rotation, no extrusion
    @pytest.fixture(scope='class')
    def insert(self):
        doc = ezdxf.new()
        blk = doc.blocks.new('POINT')
        blk.add_point(location=(0, 0))
        msp = doc.modelspace()
        return msp.add_blockref('POINT', (0, 0))

    def test_minsert_normal_spacing(self, insert):
        insert.grid(size=(2, 2), spacing=(10, 10))
        minsert = list(insert.multi_insert())
        assert len(minsert) == 4
        assert minsert[0].dxf.insert == (0, 0)
        assert minsert[1].dxf.insert == (10, 0)
        assert minsert[2].dxf.insert == (0, 10)
        assert minsert[3].dxf.insert == (10, 10)

    def test_discard_minsert_attribs_from_virtual_insert(self, insert):
        insert.grid(size=(2, 2), spacing=(10, 10))
        vinsert = next(insert.multi_insert())
        assert vinsert.dxf.hasattr('row_count') is False
        assert vinsert.dxf.hasattr('column_count') is False
        assert vinsert.dxf.hasattr('row_spacing') is False
        assert vinsert.dxf.hasattr('column_spacing') is False

    def test_minsert_zero_column_spacing(self, insert):
        insert.grid(size=(2, 2), spacing=(10, 0))
        minsert = list(insert.multi_insert())
        assert len(minsert) == 2
        assert minsert[0].dxf.insert == (0, 0)
        assert minsert[1].dxf.insert == (0, 10)

    def test_minsert_zero_row_spacing(self, insert):
        insert.grid(size=(2, 2), spacing=(0, 10))
        minsert = list(insert.multi_insert())
        assert len(minsert) == 2
        assert minsert[0].dxf.insert == (0, 0)
        assert minsert[1].dxf.insert == (10, 0)


class TestInsertAttributes:
    @pytest.fixture(scope='class')
    def insert(self):
        doc = ezdxf.new()
        blk = doc.blocks.new('POINT')
        blk.add_point(location=(0, 0))
        msp = doc.modelspace()
        insert = msp.add_blockref('POINT', (0, 0))
        insert.add_attrib('TEST', text='text', insert=(0, 0))
        return insert

    def test_attribs_transformation(self, insert):
        insert.grid(size=(2, 2), spacing=(10, 10))
        attribs = [i.attribs[0] for i in insert.multi_insert()]

        assert len(attribs) == 4
        assert len(set(id(attrib) for attrib in attribs)) == 4
        assert attribs[0].dxf.insert == (0, 0)
        assert attribs[1].dxf.insert == (10, 0)
        assert attribs[2].dxf.insert == (0, 10)
        assert attribs[3].dxf.insert == (10, 10)


class TestRotatedInsert:
    angle = 90

    @pytest.fixture(scope='class')
    def insert(self):
        doc = ezdxf.new()
        blk = doc.blocks.new('POINT')
        blk.add_point(location=(0, 0))
        msp = doc.modelspace()

        insert = msp.add_blockref('POINT', (0, 0))
        insert.dxf.rotation = self.angle
        # ATTRIB is placed outside of BLOCK in WCS, INSERT rotation is not
        # applied automatically:
        attrib = insert.add_attrib('TEST', text='text', insert=(0, 0))
        attrib.dxf.rotation = self.angle
        return insert

    def test_minsert_transformation(self, insert):
        insert.grid(size=(2, 2), spacing=(10, 10))
        minsert = list(insert.multi_insert())
        assert len(minsert) == 4
        # Rotated 90° counter clockwise:
        assert minsert[0].dxf.insert == (0, 0)
        assert minsert[1].dxf.insert == (0, 10)
        assert minsert[2].dxf.insert == (-10, 0)
        assert minsert[3].dxf.insert == (-10, 10)

    def test_attribs_transformation(self, insert):
        insert.grid(size=(2, 2), spacing=(10, 10))
        attribs = [i.attribs[0] for i in insert.multi_insert()]
        assert len(attribs) == 4
        assert len(set(id(attrib) for attrib in attribs)) == 4
        # Rotated 90° counter clockwise:
        assert attribs[0].dxf.insert == (0, 0)
        assert attribs[1].dxf.insert == (0, 10)
        assert attribs[2].dxf.insert == (-10, 0)
        assert attribs[3].dxf.insert == (-10, 10)


if __name__ == '__main__':
    pytest.main([__file__])