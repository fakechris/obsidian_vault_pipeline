---
title: "Deep GraphRAG: A Balanced Approach to Hierarchical Retrieval and Adaptive Integration"
source: "https://arxiv.org/abs/2601.11144"
source_type: arxiv-paper
source_tier: arxiv-api
extraction_status: completed
arxiv_id: 2601.11144
arxiv_categories: cs.IR, cs.AI
source_authors: ["Yuejie Li", "Ke Yang", "Tao Wang", "Bolin Chen", "Bowen Li", "Chengjun Mao"]
source_published_at: 2026-01-16
source_fetched_at: 2026-05-07T02:30:05.985929+00:00
date: 2026-05-04
type: raw
tags: [arxiv]
---

# Deep GraphRAG: A Balanced Approach to Hierarchical Retrieval and Adaptive Integration

_arXiv: [2601.11144](https://arxiv.org/abs/2601.11144)_  
_Authors: Yuejie Li, Ke Yang, Tao Wang, Bolin Chen, Bowen Li, Chengjun Mao_  
_Categories: cs.IR, cs.AI_  
_Published: 2026-01-16_

## Abstract

Graph-based Retrieval-Augmented Generation (GraphRAG) frameworks face a trade-off between the comprehensiveness of global search and the efficiency of local search. Existing methods are often challenged by navigating large-scale hierarchical graphs, optimizing retrieval paths, and balancing exploration-exploitation dynamics, frequently lacking robust multi-stage re-ranking. To overcome these deficits, we propose Deep GraphRAG, a framework designed for a balanced approach to hierarchical retrieval and adaptive integration. It introduces a hierarchical global-to-local retrieval strategy that integrates macroscopic inter-community and microscopic intra-community contextual relations. This strategy employs a three-stage process: (1) inter-community filtering, which prunes the search space using local context; (2) community-level refinement, which prioritizes relevant subgraphs via entity-interaction analysis; and (3) entity-level fine-grained search within target communities. A beam search-optimized dynamic re-ranking module guides this process, continuously filtering candidates to balance efficiency and global comprehensiveness. Deep GraphRAG also features a Knowledge Integration Module leveraging a compact LLM, trained with Dynamic Weighting Reward GRPO (DW-GRPO). This novel reinforcement learning approach dynamically adjusts reward weights to balance three key objectives: relevance, faithfulness, and conciseness. This training enables compact models (1.5B) to approach the performance of large models (70B) in the integration task. Evaluations on Natural Questions and HotpotQA demonstrate that Deep GraphRAG significantly outperforms baseline graph retrieval methods in both accuracy and efficiency.

<!-- ovp-promotions -->
> 由 OVP Pipeline 自动提取的 Evergreen 概念
- [[deep-graphrag-three-stage-hierarchical-retrieval]]
- [[deep-graphrag-benchmark-natural-questions-hotpotqa]]
<!-- /ovp-promotions -->
