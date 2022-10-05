from inductor import BartInductor

inductor = BartInductor()

rule = '<mask> is the capital of <mask>.'
generated_texts = inductor.generate(rule)

print('output generated rules:')
for text in generated_texts:
    print(text)