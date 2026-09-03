<script setup lang="ts">
import type { Narration } from '../types'

/** 单站讲解文本 + 来源署名（origin 有链接时可点击）。 */
defineProps<{ narration: Narration }>()
</script>

<template>
  <p class="narration">
    {{ narration.text }}
    <span v-if="narration.citation" class="citation">
      <template v-if="narration.citation.url">
        来源：<a :href="narration.citation.url" target="_blank" rel="noopener">{{ narration.citation.source }}</a>
      </template>
      <template v-else>来源：{{ narration.citation.source }}</template>
    </span>
  </p>
</template>

<style scoped>
.narration {
  margin: 0.3rem 0 0;
  font-family: var(--font-serif);
  color: var(--ink-soft);
  font-size: 0.85rem;
  line-height: 1.7;
}
.citation {
  font-family: var(--font-sans);
  color: var(--ink-faint);
  font-size: 0.72rem;
  margin-left: 0.4rem;
}
</style>
