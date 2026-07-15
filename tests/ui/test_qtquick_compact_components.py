import pytest

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent

from tests.conftest import QML_DIR


def _make(engine, component, props):
	assigns = '\n'.join(f'{k}: {v}' for k, v in props.items())
	qml = 'import QtQuick\nimport "."\nimport "components"\n%s { %s }' % (component, assigns)
	comp = QQmlComponent(engine)
	comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / '_probe.qml')))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	return obj


COMPONENTS = ['HeaderBar', 'Gauge', 'CookTimeBar', 'ControlPanel', 'DutyPill', 'SystemCard', 'HopperCard', 'ProbeCard']


@pytest.mark.parametrize('component', COMPONENTS)
def test_component_has_compact_property(qml_engine, component):
	obj = _make(qml_engine, component, {'compact': 'true'})
	meta = obj.metaObject()
	assert meta.indexOfProperty('compact') >= 0, f'{component} missing compact property'
	assert obj.property('compact') is True


def test_headerbar_compact_is_shorter(qml_engine):
	tall = _make(qml_engine, 'HeaderBar', {'compact': 'false'})
	short = _make(qml_engine, 'HeaderBar', {'compact': 'true'})
	assert short.property('height') < tall.property('height')
