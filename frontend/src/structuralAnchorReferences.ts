// Analyst references only. Explicit user import creates HOLD/UNREVIEWED drafts.
import type {ImageBinding,Segment,BoundaryKind} from "./structuralAnchors";
export interface AnchorReference {id:string;name:string;parent_structure_id:string;boundary_kind:BoundaryKind;ref_segment:Segment;target_segment:Segment;note:string}
export const referenceImages:{ref:ImageBinding;target:ImageBinding}={"ref": {"source_sha256": "b263554fa71eba66c0a7a28e80aaf020b3a8dfdb11acbe7c3da3130254e23b4c", "page": 4, "extraction_zoom": 2.0, "width": 2382, "height": 1684, "image_id": "b263554fa71eba66c0a7a28e80aaf020b3a8dfdb11acbe7c3da3130254e23b4c:4:2:2382x1684"}, "target": {"source_sha256": "8386a6523050f87cd7f9471213576c624e8a749428e76849dd9c4e752c17a719", "page": 4, "extraction_zoom": 2.0, "width": 2382, "height": 1684, "image_id": "8386a6523050f87cd7f9471213576c624e8a749428e76849dd9c4e752c17a719:4:2:2382x1684"}};
export const referenceReportSHA256="74e0a42c42b2120644a64dfee755d8c76ac8fa34341945f958b887357b972916";
export const anchorReferences:AnchorReference[]=[
  {
    "id": "S01",
    "name": "침실3 좌측 외벽 외측선",
    "parent_structure_id": "left_bed_wall",
    "boundary_kind": "OUTER",
    "ref_segment": [
      [
        596.3999633789062,
        965.0
      ],
      [
        596.3999633789062,
        1100.0
      ]
    ],
    "target_segment": [
      [
        596.3999633789062,
        965.0
      ],
      [
        596.3999633789062,
        1100.0
      ]
    ],
    "note": "검토 구간의 외측 위치와 방향 유지. 내측선의 0.24px 차이는 U01로 별도 검토."
  },
  {
    "id": "S02",
    "name": "우측 외벽 외측선",
    "parent_structure_id": "right_exterior",
    "boundary_kind": "OUTER",
    "ref_segment": [
      [
        1672.5599365234375,
        850.0
      ],
      [
        1672.5599365234375,
        1030.0
      ]
    ],
    "target_segment": [
      [
        1672.5599365234375,
        850.0
      ],
      [
        1672.5599365234375,
        1030.0
      ]
    ],
    "note": "길고 분산된 외측 벽선. 벽 전체·상하 개구부의 불변성을 의미하지 않음."
  },
  {
    "id": "S03",
    "name": "북측 벽의 공통 중간 구간",
    "parent_structure_id": "north_wall",
    "boundary_kind": "OUTER",
    "ref_segment": [
      [
        1040.0,
        476.080078125
      ],
      [
        1120.0,
        476.080078125
      ]
    ],
    "target_segment": [
      [
        1040.0,
        476.080078125
      ],
      [
        1120.0,
        476.080078125
      ]
    ],
    "note": "중간 구간은 유지하지만 같은 부모 벽의 우측 끝점은 M01에서 연장. 끝점과 개구부를 제외한 국소 후보."
  },
  {
    "id": "S04",
    "name": "상부 좌측 코어 외곽선 하부",
    "parent_structure_id": "shaft_outline",
    "boundary_kind": "OUTER",
    "ref_segment": [
      [
        550.3200073242188,
        440.0
      ],
      [
        550.3200073242188,
        480.0
      ]
    ],
    "target_segment": [
      [
        550.3200073242188,
        440.0
      ],
      [
        550.3200073242188,
        480.0
      ]
    ],
    "note": "초기 적합에 쓰지 않는 다른 물리 구간의 검증 후보. 전체 코어 내부·출입구 불변성은 미확정."
  },
  {
    "id": "S05",
    "name": "남측 벽체 외측선의 공통 중간",
    "parent_structure_id": "south_pier",
    "boundary_kind": "OUTER",
    "ref_segment": [
      [
        795.0,
        1183.8399658203125
      ],
      [
        860.0,
        1183.8399658203125
      ]
    ],
    "target_segment": [
      [
        795.0,
        1183.8399658203125
      ],
      [
        860.0,
        1183.8399658203125
      ]
    ],
    "note": "외측 공통부만 후보. 동일 벽체의 길이 및 내측 위치는 M02에서 차이가 있어 전체 안정으로 묶지 않음."
  },
  {
    "id": "S06",
    "name": "좌측 욕실의 유지된 기존 벽선",
    "parent_structure_id": "left_bath_existing",
    "boundary_kind": "OUTER",
    "ref_segment": [
      [
        476.6399841308594,
        705.0
      ],
      [
        476.6399841308594,
        805.0
      ]
    ],
    "target_segment": [
      [
        476.6399841308594,
        705.0
      ],
      [
        476.6399841308594,
        805.0
      ]
    ],
    "note": "기존 벽선은 실제 Target에서도 남아 있음. 새 평행선 M04와 같은 벽의 이동으로 대응시키지 않음."
  }
];
