export type DraftAnswers = Record<string, string>
export type DraftResult = { draft_answers: DraftAnswers; draft_version: number }

/** Serialize snapshots so rapid choices use the last acknowledged server version. */
export class QuizDraftQueue {
  private tail: Promise<void> = Promise.resolve()
  private version: number

  constructor(version: number, private persist: (answers: DraftAnswers, expectedVersion: number) => Promise<DraftResult>) {
    this.version = version
  }

  save(answers: DraftAnswers): Promise<DraftResult> {
    const snapshot = { ...answers }
    const task = this.tail.then(async () => {
      const result = await this.persist(snapshot, this.version)
      this.version = result.draft_version
      return result
    })
    // Each caller observes its failure; one failed save must not poison later ones.
    this.tail = task.then(() => undefined, () => undefined)
    return task
  }

  settled(): Promise<void> { return this.tail }
}
