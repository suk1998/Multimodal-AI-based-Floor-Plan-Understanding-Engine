"""Hand-authored contract fixtures. No real drawing/GT/detection is represented."""
from .examples_v0_4 import Fixture as PreviousFixture
from .schema import BASE_ELEMENT_CODES
from . import understanding_contract_v0_5 as contract


class Fixture(PreviousFixture):
    def __init__(self,role='Ref'):
        super().__init__('opening-type-contract-only',role)
        self.graph.update(schema_version=contract.VERSION,graph_id='SYNTHETIC_OPENING_TYPE_CONTRACT',
                          **{group:[] for group in contract.EXTENSION_GROUPS})

    def catalog(self,identity='catalog',origin='USER_INITIAL_GLOSSARY'):
        sha='c'*64;evidence=identity+':source'
        self.record('external_evidence',evidence,evidence_origin=origin,source_document_sha256=sha,source_page=1,
                    notice='Synthetic source metadata, not an actual schedule or independent GT.')
        return self.record('catalogs',identity,scope=identity,origin=origin,document_sha256=sha,
                           source_evidence_refs=[evidence],**({'page':1} if origin!='USER_INITIAL_GLOSSARY' else {}))

    def entry(self,code,catalog='catalog'):
        definition=next(((kind,name,mat,feature) for c,kind,name,mat,feature in BASE_ELEMENT_CODES if c==code),None)
        origin=next(c['origin'] for c in self.graph['catalogs'] if c['id']==catalog)
        family,number=contract.split_code(code)
        fields=dict(category=definition[0],name=definition[1],material=definition[2],function=definition[3]) if definition else {}
        return self.record('catalog_entries',catalog+':'+code,catalog_id=catalog,code=code,family=family,type_number=number,
            definition_status=('INITIAL_GLOSSARY' if origin=='USER_INITIAL_GLOSSARY' else 'PROJECT_DECLARED') if definition else 'UNRESOLVED',
            source_evidence_refs=[catalog+':source'] if definition else [],**fields)

    def marker(self,identity,code,x=20,entry_id=None):
        family,number=contract.split_code(code)
        self.record('observations',identity+':letters',observation_type='TextRegion',text=family,
                    geometry=self.geometry('Polygon',[[x,30],[x+10,30],[x+10,40],[x,40]]))
        self.record('observations',identity+':number',observation_type='TextRegion',text=number,
                    geometry=self.geometry('Polygon',[[x+3,20],[x+7,20],[x+7,29],[x+3,29]]))
        refs=[identity+':letters',identity+':number']
        self.record('entities',identity,expected_type='Symbol',grounding_status='PARTIAL',geometry_state='POLYGON_AVAILABLE',
                    geometry=self.geometry('Polygon',[[x-3,17],[x+13,17],[x+13,43],[x-3,43]]),source_evidence_refs=refs)
        return self.record('type_designations',identity+':designation',symbol_id=identity,family=family,type_number=number,
            canonical_code=code,entry_id=entry_id or 'catalog:'+code,reading_status='COMPLETE',status='SUPPORTED',
            reading_observation_ids=refs,source_evidence_refs=refs)


def printed_types_fixture():
    f=Fixture();f.catalog()
    for code in ['PD/1','PD/2','PW/1','AG/1','FSD/1']:f.entry(code)
    for i,code in enumerate(['PD/1','PD/1','PD/2','PW/1','AG/1','FSD/1']):f.marker('symbol'+str(i),code,x=10+14*i)
    return f.graph


def assigned_door_fixture():
    f=Fixture();f.catalog(origin='PROJECT_SCHEDULE');f.entry('PD/2');designation=f.marker('symbol','PD/2')
    f.record('observations','door-geometry',observation_type='DoorSwing',geometry=f.geometry('Segment',[[50,30],[50,60]]))
    f.record('observations','leader',observation_type='LeaderLine',geometry=f.geometry('Segment',[[32,30],[50,40]]))
    f.record('entities','door',expected_type='Door',grounding_status='GROUNDED',geometry_state='PARTIAL_BOUNDARY',
        geometry=f.geometry('Segment',[[50,30],[50,60]]),source_evidence_refs=['door-geometry'])
    f.record('entities','opening',expected_type='Opening',grounding_status='PARTIAL',geometry_state='PARTIAL_BOUNDARY',
        geometry=f.geometry('Segment',[[50,30],[50,60]]),source_evidence_refs=['door-geometry'])
    f.record('entities','wall',expected_type='Wall',grounding_status='PARTIAL',geometry_state='PARTIAL_BOUNDARY',
        geometry=f.geometry('Segment',[[50,10],[50,90]]),source_evidence_refs=['door-geometry'])
    f.record('relations','reference',kind='ReferenceAssociation',source_id='symbol',target_id='door',grounding_status='GROUNDED',
        reference_method='TRACED_LEADER',source_evidence_refs=['leader'])
    f.record('relations','installation',kind='Installation',source_id='door',target_id='opening',source_evidence_refs=['door-geometry'])
    f.record('relations','hosting',kind='Hosting',source_id='wall',target_id='opening',source_evidence_refs=['door-geometry'])
    f.record('type_assignments','assignment',element_id='door',designation_id=designation['id'],entry_id='catalog:PD/2',
        reference_relation_id='reference',status='SUPPORTED',scope='CATALOG_TYPE_ONLY',source_evidence_refs=['leader','door-geometry'])
    return f.graph
