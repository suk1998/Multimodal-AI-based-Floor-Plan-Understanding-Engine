"""Workspace-owned review revisions and final deliverables for a comparison."""
from collections import Counter
import csv
from dataclasses import dataclass, field
import hashlib
from io import BytesIO, StringIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
import numpy as np
import pymupdf
from backend.change_policy import is_supported_annotation_change, is_final_change_kind
from backend.door_change_policy import is_door_drawing_row,is_supported_door_change
from backend.boundary_aperture_comparison import is_boundary_aperture_row,is_supported_boundary_aperture_change
from integrations.fpue_ontology.opening_change_contract import is_opening_drawing_row,is_supported_opening_change
from backend.upstream_lineage import public_comparison


FIELDS = ('run_id', 'change_id', 'region_id', 'domain', 'state', 'change_types',
          'target_page', 'x1', 'y1', 'x2', 'y2', 'correspondence_ids', 'evidence_refs', 'decision_reason',
          'space_name', 'element', 'change_description', 'decision_source',
          'before', 'after', 'ref_frame_id', 'target_frame_id', 'ref_bbox_native',
          'target_bbox_native', 'physical_change_inferred')


@dataclass
class ComparisonReview:
    comparison: dict
    documents: list
    revision: int = 0
    decisions: dict = field(default_factory=dict)

    def view(self):
        return dict(comparison=public_comparison(self.comparison), documents=self.documents,
                    revision=self.revision, decisions=self.decisions)

    def confirmable(self, row):
        if is_boundary_aperture_row(row) and not is_supported_boundary_aperture_change(row):return False
        if is_door_drawing_row(row) and not is_supported_door_change(row):return False
        if ((row.get('entity_type') in ('DimensionText','CodeMarker') and not is_supported_annotation_change(row))
            or (is_opening_drawing_row(row) and not is_supported_opening_change(row))):
            return False
        return (self.comparison['alignment_status'] in ('VERIFIED_ROI', 'PARTIAL')
                and row['domain'] in ('STRUCTURAL', 'ANNOTATION')
                and row['judgment'] == 'CHANGED' and not row['alignment_fit_parent']
                and row.get('verified_target_bbox_native') is not None)

    def state(self, row):
        if row['correspondence_id'] in self.decisions:
            return self.decisions[row['correspondence_id']]
        return 'CONFIRMED' if row.get('automatic_confirmation') and self.confirmable(row) else 'REVIEW'

    def save(self, decisions):
        rows = {row['correspondence_id']: row for row in self.comparison['correspondences']}
        if not isinstance(decisions, dict) or not decisions.keys() <= rows.keys():
            raise ValueError('현재 비교에 없는 검토 사례입니다.')
        for key, state in decisions.items():
            if state not in ('CONFIRMED', 'DISMISSED'):
                raise ValueError('검토 상태를 확인하세요.')
            if state == 'CONFIRMED' and not self.confirmable(rows[key]):
                raise ValueError('검증 범위 안의 변경 후보만 확정할 수 있습니다.')
        self.decisions = decisions.copy()
        self.revision += 1
        return self.view()


def csv_bytes(records):
    output = StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    for record in records:
        # A text cell may contain a PDF label or another user-controlled value.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v
                         for k, v in record.items()})
    return output.getvalue().encode('utf-8-sig')


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def marked_png(page, records):
    if not records:
        return page.png
    image = cv2.imdecode(np.frombuffer(page.png, dtype=np.uint8), cv2.IMREAD_COLOR)
    # The built-in Korean PDF font also works on hosts without system fonts.
    with pymupdf.open() as doc:
        label_page = doc.new_page(width=37, height=21)
        label_page.insert_text((2, 15), '변경', fontsize=13, fontname='korea')
        glyphs = label_page.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
        label_mask = np.frombuffer(glyphs.samples, np.uint8).reshape(glyphs.height, glyphs.width) < 200
    w, h = page.width, page.height
    blue = (220, 74, 0)
    for record in records:
        x1, y1, x2, y2 = (record[k] for k in ('x1', 'y1', 'x2', 'y2'))
        cv2.rectangle(image, (x1, y1), (min(w-1, x2), min(h-1, y2)), blue, 2)
    for record in records:
        lh, lw = label_mask.shape
        x = max(0, min(w-lw, record['x1']))
        y = max(0, min(h-lh, record['y1']-lh if record['y1']>=lh else record['y2']))
        patch = image[y:y+lh, x:x+lw]
        patch[:] = (255, 255, 255)
        patch[label_mask[:patch.shape[0], :patch.shape[1]]] = blue
    ok, result = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('변경 PNG를 생성하지 못했습니다.')
    return result.tobytes()


def export_review(review, target, format='zip'):
    result = review.comparison
    structural, dimensions, symbols, final_records, pending, annotation = [], [], [], [], [], []
    target_objects = {o['id']: o for o in result['source_objects']['Target']}
    for row in result['correspondences']:
        key = row['correspondence_id']
        state = review.state(row)
        bbox = row.get('verified_target_bbox_native')
        if bbox is None and row['target_ids']:
            boxes = np.asarray([target_objects[v]['bbox'] for v in row['target_ids']])
            bbox = [*boxes[:, :2].min(axis=0), *boxes[:, 2:].max(axis=0)]
        bounds = ([int(np.floor(v)) for v in bbox[:2]] + [int(np.ceil(v)) for v in bbox[2:]]
                  if bbox is not None else [None]*4)
        record = dict(run_id=result['run_id'], change_id=key, region_id=result['input_fingerprint'],
                      domain=row['domain'], state=state, change_types='|'.join(row['change_types']),
                      target_page=target.page_number, correspondence_ids=key,
                      evidence_refs=json.dumps(row['evidence_refs'], ensure_ascii=False),
                      decision_reason=row['decision_reason'],
                      space_name=row.get('space_name') or '미확인', element=row.get('entity_type',''),
                      change_description=row.get('change_description',row['decision_reason']),
                      decision_source='USER_REVIEW' if key in review.decisions else
                          row.get('automatic_confirmation','ANALYSIS'),
                      before=row.get('before',''), after=row.get('after',''),
                      ref_frame_id=result['sources'][0]['frame_id'],
                      target_frame_id=result['sources'][1]['frame_id'],
                      ref_bbox_native=json.dumps(row['ref_bbox_native']) if row.get('ref_bbox_native') is not None else '',
                      target_bbox_native=json.dumps(row.get('target_observed_bbox_native',bbox)) if bbox is not None else '',
                      physical_change_inferred=row.get('physical_change_inferred',''))
        record.update(zip(('x1', 'y1', 'x2', 'y2'), bounds))
        if state == 'CONFIRMED' and review.confirmable(row) and is_final_change_kind(row):
            final_records.append(record)
            (structural if row['domain']=='STRUCTURAL' else dimensions if row.get('entity_type')=='DimensionText' else symbols).append(record)
        elif row['domain']=='ANNOTATION' and row['judgment']=='CHANGED':
            annotation.append(record)
        else:
            pending.append(record)

    if format == 'png':
        return marked_png(target, final_records)
    if format == 'csv':
        return csv_bytes(final_records)
    files = {'target_changes.png': marked_png(target, final_records),
             'change_report.csv': csv_bytes(final_records),
             'review_candidates.csv': csv_bytes(pending),
             'comparison.json': json_bytes({**result, 'review_revision':review.revision,
                 'review_decisions': review.decisions, 'confirmed_changes':[r['change_id'] for r in final_records]})}
    if annotation:
        files['annotation_changes.csv'] = csv_bytes(annotation)
    unresolved = any(r['judgment']=='UNDETERMINED' for r in result['correspondences'])
    manifest = dict(schema_version='fpue_comparison_export/1', run_id=result['run_id'],
                    alignment_status=result['alignment_status'], analysis_status=result['status'],
                    review_revision=review.revision, sources=result['sources'], target_roi=result['target_roi'],
                    coordinate_system='image_native_pixels', bbox_convention='xyxy_outer_pixel_edges',
                    review_state_counts=dict(Counter(review.state(r)
                                                    for r in result['correspondences'])),
                    confirmed_structural_count=len(structural),
                    confirmed_dimension_text_count=len(dimensions), confirmed_symbol_text_count=len(symbols), confirmed_change_count=len(final_records),
                    unresolved_comparison=unresolved or result['status']=='UNDETERMINED' or result['alignment_status']!='VERIFIED_ROI',
                    files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    files['manifest.json'] = json_bytes(manifest)
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()
