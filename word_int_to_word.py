import codecs

index_to_word = 'index_to_word.txt'
sample_file = 'save/pre-train-sample.txt'

int_to_word=[]
in_w = codecs.open(index_to_word,'r', 'utf-8')
for w in in_w.readlines():
    int_to_word.append(w.strip())
in_w.close()

sample = codecs.open(sample_file, 'r', 'utf-8')

for line in sample.readlines():
    idx = line.strip().split()
    str = ""
    for id in idx:
        str = str + int_to_word[int(id)] + " "
    print(str)

