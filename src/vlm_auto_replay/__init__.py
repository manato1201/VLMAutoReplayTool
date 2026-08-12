"""VLMAutoReplayTool: 基盤モデルによる計画と専用モデルによる実行を分離したゲーム自動プレイエージェント。

サブパッケージ構成(設計書のPhase対応):
- prompts:    Phase1 プロンプトテンプレート層(9つのプロンプト意図)
- loop:       Phase2 メインループ+StepLog
- actions:    Phase3 Action実行層(Skill/API二層+HID)
- knowledge:  Phase4 知識ソース+RAG(TODO生成時限定)
- skills:     Phase6 スキル自動抽出
- navigation: Phase7 ナビゲーション推論(視覚的自己位置推定)
"""
